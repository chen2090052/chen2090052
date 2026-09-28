"""MCP tool implementations.

Each module here owns one tool: its Pydantic input/output models, its
description, and a ``register_<tool>`` function, over the shared vocabulary in
``_shared``.

Adding a tool means adding a module, adding one entry to
:data:`TOOL_INPUT_MODELS`, and one call in :func:`register_tools`. The REST
client, the logging convention and the error contract are reused unchanged.
"""

from collections.abc import Mapping
from typing import Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel

from pet_hospital_mcp.rest_client import PetHospitalRestClient
from pet_hospital_mcp.tools.add_pet_charge import (
    ADD_PET_CHARGE_INPUT_MODEL,
    ADD_PET_CHARGE_TOOL_NAME,
    AddPetChargeInput,
    AddPetChargeOutput,
    register_add_pet_charge,
)
from pet_hospital_mcp.tools.add_pet_record import (
    ADD_PET_RECORD_INPUT_MODEL,
    ADD_PET_RECORD_TOOL_NAME,
    AddPetRecordInput,
    AddPetRecordOutput,
    register_add_pet_record,
)
from pet_hospital_mcp.tools.get_endpoints import (
    GET_ENDPOINTS_INPUT_MODEL,
    GET_ENDPOINTS_TOOL_NAME,
    GetEndpointsOutput,
    register_get_endpoints,
)
from pet_hospital_mcp.tools.get_meta import (
    GET_META_INPUT_MODEL,
    GET_META_TOOL_NAME,
    GetMetaOutput,
    register_get_meta,
)
from pet_hospital_mcp.tools.get_pet import (
    GET_PET_INPUT_MODEL,
    GET_PET_TOOL_NAME,
    GetPetInput,
    GetPetOutput,
    register_get_pet,
)
from pet_hospital_mcp.tools.get_pet_summary import (
    GET_PET_SUMMARY_INPUT_MODEL,
    GET_PET_SUMMARY_TOOL_NAME,
    GetPetSummaryInput,
    PetSummary,
    register_get_pet_summary,
)
from pet_hospital_mcp.tools.get_stats import (
    GET_STATS_INPUT_MODEL,
    GET_STATS_TOOL_NAME,
    GetStatsInput,
    GetStatsOutput,
    register_get_stats,
)
from pet_hospital_mcp.tools.list_pet_charges import (
    LIST_PET_CHARGES_INPUT_MODEL,
    LIST_PET_CHARGES_TOOL_NAME,
    ListPetChargesInput,
    ListPetChargesOutput,
    register_list_pet_charges,
)
from pet_hospital_mcp.tools.list_pet_records import (
    LIST_PET_RECORDS_INPUT_MODEL,
    LIST_PET_RECORDS_TOOL_NAME,
    ListPetRecordsInput,
    ListPetRecordsOutput,
    register_list_pet_records,
)
from pet_hospital_mcp.tools.list_pets import (
    LIST_PETS_INPUT_MODEL,
    LIST_PETS_TOOL_NAME,
    ListPetsInput,
    ListPetsOutput,
    register_list_pets,
)

#: Tool name -> strict input model. Consulted by the server middleware to
#: validate raw arguments before the tool body runs.
TOOL_INPUT_MODELS: Mapping[str, type[BaseModel]] = {
    GET_ENDPOINTS_TOOL_NAME: GET_ENDPOINTS_INPUT_MODEL,
    GET_META_TOOL_NAME: GET_META_INPUT_MODEL,
    GET_PET_TOOL_NAME: GET_PET_INPUT_MODEL,
    GET_PET_SUMMARY_TOOL_NAME: GET_PET_SUMMARY_INPUT_MODEL,
    GET_STATS_TOOL_NAME: GET_STATS_INPUT_MODEL,
    LIST_PET_CHARGES_TOOL_NAME: LIST_PET_CHARGES_INPUT_MODEL,
    LIST_PET_RECORDS_TOOL_NAME: LIST_PET_RECORDS_INPUT_MODEL,
    LIST_PETS_TOOL_NAME: LIST_PETS_INPUT_MODEL,
    ADD_PET_CHARGE_TOOL_NAME: ADD_PET_CHARGE_INPUT_MODEL,
    ADD_PET_RECORD_TOOL_NAME: ADD_PET_RECORD_INPUT_MODEL,
}


def register_tools(mcp: MCPServer, client: PetHospitalRestClient) -> None:
    """Register every tool on ``mcp``, bound to ``client``."""
    register_get_endpoints(mcp, client)
    register_get_meta(mcp, client)
    register_get_pet(mcp, client)
    register_get_pet_summary(mcp, client)
    register_get_stats(mcp, client)
    register_list_pet_charges(mcp, client)
    register_list_pet_records(mcp, client)
    register_list_pets(mcp, client)
    register_add_pet_charge(mcp, client)
    register_add_pet_record(mcp, client)


__all__ = [
    "ADD_PET_CHARGE_INPUT_MODEL",
    "ADD_PET_CHARGE_TOOL_NAME",
    "ADD_PET_RECORD_INPUT_MODEL",
    "ADD_PET_RECORD_TOOL_NAME",
    "GET_ENDPOINTS_INPUT_MODEL",
    "GET_ENDPOINTS_TOOL_NAME",
    "GET_META_INPUT_MODEL",
    "GET_META_TOOL_NAME",
    "GET_PET_INPUT_MODEL",
    "GET_PET_SUMMARY_INPUT_MODEL",
    "GET_PET_SUMMARY_TOOL_NAME",
    "GET_PET_TOOL_NAME",
    "GET_STATS_INPUT_MODEL",
    "GET_STATS_TOOL_NAME",
    "LIST_PET_CHARGES_INPUT_MODEL",
    "LIST_PET_CHARGES_TOOL_NAME",
    "LIST_PET_RECORDS_INPUT_MODEL",
    "LIST_PET_RECORDS_TOOL_NAME",
    "LIST_PETS_INPUT_MODEL",
    "LIST_PETS_TOOL_NAME",
    "AddPetChargeInput",
    "AddPetChargeOutput",
    "AddPetRecordInput",
    "AddPetRecordOutput",
    "GetPetInput",
    "GetPetOutput",
    "GetPetSummaryInput",
    "GetStatsInput",
    "GetStatsOutput",
    "ListPetChargesInput",
    "ListPetChargesOutput",
    "ListPetRecordsInput",
    "ListPetRecordsOutput",
    "ListPetsInput",
    "ListPetsOutput",
    "PetSummary",
    "TOOL_INPUT_MODELS",
    "register_add_pet_charge",
    "register_add_pet_record",
    "register_get_endpoints",
    "register_get_meta",
    "register_get_pet",
    "register_get_pet_summary",
    "register_get_stats",
    "register_list_pet_charges",
    "register_list_pet_records",
    "register_list_pets",
    "register_tools",
]