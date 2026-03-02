class _Registry:
    def register(self):
        def deco(obj):
            return obj
        return deco

OPERATOR_REGISTRY = _Registry()
PROMPT_REGISTRY = _Registry()

