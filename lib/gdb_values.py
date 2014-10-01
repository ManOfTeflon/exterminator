import gdb

def get_str(value):
    try:
        s = "Cannot decode object"
        for encoding in [ 'utf8', 'ascii' ]:
            try:
                s = '"%s"' % value.string(encoding).encode("unicode-escape")
                break
            except UnicodeDecodeError:
                pass
        return s
    except gdb.error as e:
        return str(e)
    except gdb.MemoryError as e:
        return str(e)

def gdb_to_py(name, value):
    t = value.type
    typename = str(t)

    fullname = name
    name = "%s (%s)" % (name, typename)

    def transform(name, fullname, value, t):
        while True:
            matches = [ transform for filt, transform in transforms if filt(value, t) ]
            if len(matches) == 0:
                break
            name, fullname, value, t = matches[0](name, fullname, value, t)
        return name, fullname, value, t

    def get_converter(t):
        for filt, converter in converters:
            if filt(t):
                return converter

    def is_one_liner(value, t):
        _, _, value, t = transform('', '', value, t)
        return get_converter(t) in [ one_liner_to_py, string_to_py ]

    def string_to_py(name, fullname, value, t):
        return { name + ': ' + get_str(value): 0 }

    def struct_to_py(name, fullname, value, t):
        def _struct_to_py(field):
            contents = {}
            for sub_field in field.fields():
                sub_type = sub_field.type
                sub_field_name = "%s (%s)" % (sub_field.name, sub_type)
                if sub_field.is_base_class:
                    this = { sub_field.name: "static_cast<%s >(%s)" % (sub_type, fullname) }
                else:
                    if sub_field.name:
                        try:
                            assert(is_one_liner(value[sub_field.name], sub_type))
                            this = gdb_to_py(sub_field.name, value[sub_field.name])
                        except gdb.error as e:
                            this = { sub_field_name + ': ' + str(e): 0 }
                        except:
                            this = { sub_field_name: fullname + '.' + sub_field.name }
                    else:
                        this = { sub_field_name: _struct_to_py(sub_type) }
                contents = dict(contents, **this)
            return contents

        return { name: _struct_to_py(t) }

    def array_to_py(name, fullname, value, t):
        size = t.sizeof / t.target().sizeof
        contents = {}
        elem_typename = str(t.target())
        for i in xrange(min(size, 10)):
            elem_name = "[%d]" % (i)
            if is_one_liner(value[i], t.target()):
                this = gdb_to_py(elem_name, value[i])
            else:
                this = { "%s (%s)" % (elem_name, elem_typename): "%s[%d]" % (fullname, i) }
            contents = dict(contents, **this)
        if size > 10:
            contents["And %d more..." % (size - 10)] = 0
        return { name: contents }

    def one_liner_to_py(name, fullname, value, t):
        s = u"%s" % value
        return { name + ': ' + s: 0 }

    def atomic_transform(name, fullname, value, t):
        new_type = t.template_argument(0)
        return name, fullname, value['m_value']['v_'].cast(new_type), new_type

    def vector_transform(name, fullname, value, t):
        void_p = gdb.lookup_type('void').pointer()
        start = int(str(value['_M_impl']['_M_start'].cast(void_p)), 16)
        finish = int(str(value['_M_impl']['_M_finish'].cast(void_p)), 16)
        length = finish - start
        if length == 0:
            return name, fullname, 'empty', None
        new_type = t.template_argument(0).array(length - 1)
        return name, fullname + '._M_impl._M_start', value['_M_impl']['_M_start'].cast(new_type), new_type

    def ptr_transform(name, fullname, value, t):
        return name + (' @%s' % value), fullname, value.dereference(), t.target()

    def ref_transform(name, fullname, value, t):
        return name + (' @%s' % value.address), fullname, value.cast(t.target()), t.target()

    def null_transform(name, fullname, value, t):
        return name, fullname, 'nullptr', None

    def typedef_transform(name, fullname, value, t):
        return name, fullname, value, t.strip_typedefs()

    transforms = [
        (lambda value, t: t is not None and
                   t.code == gdb.TYPE_CODE_REF,
            ref_transform),
        (lambda value, t: t is not None and
                   t.code == gdb.TYPE_CODE_TYPEDEF,
            typedef_transform),
        (lambda value, t: t is not None and
                   t.code == gdb.TYPE_CODE_PTR and
                   str(value) == '0x0',
            null_transform),
        (lambda value, t: t is not None and
                   t.code == gdb.TYPE_CODE_PTR and
                   str(t.target().unqualified()) != 'char',
            ptr_transform),
        (lambda value, t: t is not None and
                   t.code == gdb.TYPE_CODE_STRUCT and
                   str(t).startswith("SimpleAtomic"),
            atomic_transform),
        (lambda value, t: t is not None and
                   t.code == gdb.TYPE_CODE_STRUCT and
                   str(t).startswith("std::vector"),
            vector_transform),
    ]

    converters = [
        (lambda t: t is not None and
                   t.code == gdb.TYPE_CODE_PTR and
                   str(t.target().unqualified()) == 'char',
            string_to_py),
        (lambda t: t is not None and
                   t.code in [ gdb.TYPE_CODE_STRUCT, gdb.TYPE_CODE_UNION ],
            struct_to_py),
        (lambda t: t is not None and
                   t.code == gdb.TYPE_CODE_ARRAY and
                   str(t.target().unqualified()) != 'char',
            array_to_py),
        (lambda t: True,
            one_liner_to_py),
    ]

    try:
        name, fullname, value, t = transform(name, fullname, value, t)
        return get_converter(t)(name, fullname, value, t)
    except gdb.error as e:
        return { name: { str(e) : 0 } }
    except:
        import traceback
        lines = traceback.format_exc().split('\n')
        p = len("%d" % len(lines))
        return { "Python server error": { "%0*d: %s" % (p, i, line): 0 for i, line in enumerate(lines) } }

def locals_to_py():
    variables = [ 'this' ]
    variables += [ a.split(' = ', 1)[0] for a in gdb.execute("info args", to_string=True).split('\n')[:-1] ]
    variables += [ l.split(' = ', 1)[0] for l in gdb.execute("info locals", to_string=True).split('\n')[:-1] ]
    contents = {}
    for var in variables:
        try:
            value = gdb.parse_and_eval(var)

        except gdb.error as e:
            if var != 'this':
                contents = dict(contents, **{ var: { str(e): 0 } })

        else:
            contents = dict(contents, **gdb_to_py(var, value))
    return contents

