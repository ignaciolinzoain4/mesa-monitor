"""El contrato de eventos: schemas/ al día y el cliente Python sincronizado con el servidor."""

from typing import Any

from pydantic import BaseModel
from tools.export_schemas import main as export_main

import mesa_monitor_client.models as client_models
from mesa_monitor.core import events as server


def test_schemas_directory_is_up_to_date(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.argv", ["export_schemas", "--check"])
    assert export_main() == 0


def _fields(model: type[BaseModel]) -> dict[str, bool]:
    return {name: f.is_required() for name, f in model.model_fields.items()}


def test_client_models_match_server_events() -> None:
    for name, server_model in server.EVENT_MODELS.items():
        client_cls = next(
            c
            for c in vars(client_models).values()
            if isinstance(c, type)
            and issubclass(c, BaseModel)
            and c.model_fields.get("type") is not None
            and c.model_fields["type"].default == name
        )
        assert _fields(client_cls) == _fields(server_model), name
        s_data = server_model.model_fields["data"].annotation
        c_data = client_cls.model_fields["data"].annotation
        if isinstance(s_data, type) and issubclass(s_data, BaseModel):
            assert isinstance(c_data, type)
            assert issubclass(c_data, BaseModel)
            assert set(c_data.model_fields) == set(s_data.model_fields), name
    assert list(server.EVENT_MODELS) == client_models.EVENT_TYPES
