.claude/skills/ifcopenshell-ops.md

# Skill: IfcOpenShell Operations

Read before writing any IFC modification code.

## Transactions
model.begin_transaction() / model.end_transaction() / model.undo()

## Property Operations
- SET: prop.NominalValue = model.create_entity("IfcLabel", "EI120")
- ADD to pset: ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={...})
- ADD pset: ifcopenshell.api.run("pset.add_pset", model, product=entity, name="Pset_X")
- REMOVE: properties={"PropName": None}

## Entity Operations (Tier 3 only)
- Create: ifcopenshell.api.run("root.create_entity", model, ifc_class="IfcSpace")
- Delete: ifcopenshell.api.run("root.remove_product", model, product=entity)
- Assign spatial: ifcopenshell.api.run("spatial.assign_container", model, ...)

## Key Rules
- Tier 3 code: never call model.write() or ifcopenshell.open()
- Property name matching is always case-insensitive
- Record every change in changes list for Git traceability
- Type coercion: IfcBoolean for bool, IfcReal for float, IfcLabel for string

---

