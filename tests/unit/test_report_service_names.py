"""cmd_import_mmr shipped referencing a name bound only in another function, so it
raised NameError before reaching the database and no test noticed: the mmr tests
skip report_service entirely because it imports the Unix-only `pwd` module.

symtable reads the source without importing it, so this runs everywhere.
"""
from __future__ import annotations

import builtins
import symtable
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "scripts" / "report_service.py"


def _module_bindings(table: symtable.SymbolTable) -> set[str]:
    return {s.get_name() for s in table.get_symbols()
            if s.is_assigned() or s.is_imported() or s.is_namespace()}


def _function(table: symtable.SymbolTable, name: str) -> symtable.SymbolTable:
    for child in table.get_children():
        if child.get_name() == name:
            return child
    raise AssertionError(f"{name} is not a top-level function of {SOURCE.name}")


def test_cmd_import_mmr_references_no_name_it_cannot_resolve():
    top = symtable.symtable(SOURCE.read_text(encoding="utf-8"), str(SOURCE), "exec")
    bound = _module_bindings(top)
    fn = _function(top, "cmd_import_mmr")
    unresolved = sorted(
        s.get_name() for s in fn.get_symbols()
        if s.is_referenced() and not s.is_assigned() and not s.is_parameter()
        and not s.is_imported()  # this module imports inside functions
        and s.get_name() not in bound and not hasattr(builtins, s.get_name())
    )
    assert not unresolved, (
        "cmd_import_mmr references %s, which is bound in no enclosing scope. "
        "`kind` was such a name: it came along when the body was copied from "
        "cmd_aggregate, where it is the loop variable." % unresolved)
