# config_manager/vad.py
from typing import ClassVar, Dict

from pydantic import Field, model_validator

from .i18n import Description, I18nMixin


class VADConfig(I18nMixin):
    vad_model: str | None = Field(None, alias="vad_model")

    DESCRIPTIONS: ClassVar[Dict[str, Description]] = {
        "vad_model": Description(
            en="Voice Activity Detection model to use",
            zh="VAD model",
        ),
    }

    @model_validator(mode="after")
    def check_vad_config(self):
        if self.vad_model is not None:
            raise ValueError("VAD providers were removed from this project cleanup.")

        return self
