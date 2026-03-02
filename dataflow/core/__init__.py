class OperatorABC:
    def run(self, *args, **kwargs):
        raise NotImplementedError

class LLMServingABC:
    def generate_from_input(self, inputs):
        raise NotImplementedError

