from .vad_interface import VADInterface


class VADFactory:
    @staticmethod
    def get_vad_engine(engine_type, **kwargs) -> VADInterface | None:
        if engine_type is None:
            return None

        raise ValueError(f"Unknown VAD engine type: {engine_type}")
