from gdo.core.GDT_String import GDT_String


class GDT_Phone(GDT_String):
    def __init__(self, name: str = 'phone'):
        super().__init__(name)
        self.ascii().minlen(7).maxlen(32).pattern(r'^[0-9]{7,32}$')
        self._input_type = 'tel'

    def val(self, val: str | list):
        if type(val) is str:
            normalized = self.normalize_number(val)
            if normalized is not None:
                val = normalized
        return super().val(val)

    @staticmethod
    def normalize_number(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if value.startswith('+'):
            value = f'00{value[1:]}'
        if not value or '+' in value or any(char not in '0123456789 ()-./' for char in value):
            return None
        number = ''.join(char for char in value if char.isdigit())
        return number if 7 <= len(number) <= 32 else None
