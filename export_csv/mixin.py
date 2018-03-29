from __future__ import unicode_literals
from __future__ import absolute_import
import datetime
import unicodecsv as csv
import codecs

from django.http import StreamingHttpResponse

from export_csv import clean_filename, attach_datestamp, generate_filename, Echo, get_uncontain_field_names


class QueryCsvMixin(object):
    filename = None
    add_datestamp = False
    use_verbose_names = True
    fields = []
    exclude = []
    field_order = []
    field_header_map = {}
    field_serializer_map = {}
    extra_field = []

    def render_csv_response(self, queryset):
        """
        making a CSV streaming http response, take a queryset
        """
        if self.filename:
            filename = clean_filename(self.filename)
            if self.add_datestamp:
                filename = attach_datestamp(filename)
        else:
            filename = generate_filename(queryset, self.add_datestamp)

        response_args = {'content_type': 'text/csv'}

        response = StreamingHttpResponse(
            self._iter_csv(queryset, Echo()), **response_args)

        # support chinese filename
        response['Content-Disposition'] = b'attachment; filename=' + filename.encode(encoding='utf-8') + b';'
        response['Cache-Control'] = 'no-cache'

        return response

    def _iter_csv(self, queryset, file_obj):
        """
        Writes CSV data to a file object based on the
        contents of the queryset and yields each row.
        """
        csv_kwargs = {'encoding': 'utf-8'}

        # add BOM to support MS Excel (for Windows only)
        yield file_obj.write(codecs.BOM_UTF8)

        if type(queryset).__name__ == 'ValuesQuerySet':
            queryset_values = queryset
        else:
            queryset_values = queryset.values()

        if self.fields:
            uncontain_field_names = get_uncontain_field_names(
                self.fields, queryset_values.field_names
            )
            if uncontain_field_names:
                raise Exception(','.join(uncontain_field_names) + " aren't in default field names")

            field_names = self.fields
        elif self.exclude:
            field_names = [
                f.name
                for f in queryset.model._meta.get_fields()
                if not (f.is_relation or f.one_to_one or (f.many_to_one and f.related_model))
                   and f.name not in self.exclude
            ]
        else:
            field_names = [f.name for f in queryset.model._meta.get_fields()
                           if not (f.is_relation
                                   or f.one_to_one
                                   or (f.many_to_one and f.related_model))]

        field_names += self.extra_field

        field_names = [field_name for field_name in field_names if field_name in self.field_order] + \
                      [field_name for field_name in field_names if field_name not in self.field_order]

        # support extra
        # Sometimes, the Django query syntax by itself can't easily express a complex WHERE clause.
        # For these edge cases, Django provides the extra() QuerySet modifier
        # a hook for injecting specific clauses into the SQL generated by a QuerySet.
        extra_columns = list(queryset_values.query.extra_select)
        if extra_columns:
            field_names += extra_columns

        # support annotate
        # Annotates each object in the QuerySet with the provided list of query expressions.
        # An expression may be a simple value, a reference to a field on the model (or any related models),
        # or an aggregate expression (averages, sums, etc) that
        # has been computed over the objects that are related to the objects in the QuerySet.
        annotation_columns = list(queryset_values.query.annotation_select)
        if annotation_columns:
            field_names += annotation_columns

        writer = csv.DictWriter(file_obj, field_names, **csv_kwargs)

        header_map = dict((field, field) for field in field_names)
        if self.use_verbose_names:
            for field in queryset.model._meta.get_fields():
                if field.name in field_names:
                    try:
                        header_map[field.name] = field.verbose_name
                    except AttributeError:
                        header_map[field.name] = field.name

        header_map.update(self.field_header_map)

        yield writer.writerow(header_map)

        if self.extra_field:
            model = queryset_values.model
            for item in queryset_values:
                item = self._sanitize_related_item(self.field_serializer_map, item, model)
                item = self._sanitize_item(self.field_serializer_map, item, field_names)
                yield writer.writerow(item)
        else:
            for item in queryset_values:
                item = self._sanitize_item(self.field_serializer_map, item, field_names)
                yield writer.writerow(item)

    def _sanitize_item(self, field_serializer_map, item, field_names):
        def _serialize_value(value):
            if isinstance(value, datetime.datetime):
                return value.isoformat()
            else:
                return str(value)

        obj = {}
        for key, val in item.items():
            if key in self.exclude:
                continue
            if key in field_names or key in self.extra_field:
                if key in self.extra_field:
                    obj[key] = val
                    continue
                if val is not None:
                    serializer = field_serializer_map.get(key, _serialize_value)
                    newval = serializer(val)
                    if not isinstance(newval, str):
                        newval = str(newval)
                    obj[key] = newval
        return obj

    def _sanitize_related_item(self, field_serializer_map, item, model):
        obj = model.objects.get(id=item['id'])
        for field_name in self.extra_field:
            serializer = field_serializer_map.get(field_name)
            item[field_name] = serializer(obj)
        return item