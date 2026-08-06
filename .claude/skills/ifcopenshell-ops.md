.claude/skills/ifcopenshell-ops.md

# Skill: IfcOpenShell Operations

Read before writing any IFC modification code.

## `products` vs `product` — the #1 source of silent bugs

IfcOpenShell 0.8.x collection APIs take **`products` (a LIST)**. Passing the
singular `product=` raises `TypeError`, and this codebase has shipped that
bug twice because a broad `except` swallowed it. Never guess — the exact
signatures are below.

Plural `products=[...]`: `spatial.assign_container`, `spatial.unassign_container`,
`aggregate.assign_object`, `aggregate.unassign_object`, `group.assign_group`,
`group.unassign_group`, `material.assign_material`, `classification.add_reference`.

Singular `product=`: `attribute.edit_attributes`, `pset.add_pset`,
`root.remove_product`. (Yes, it is inconsistent. Check, don't assume.)

## Transactions
`model.begin_transaction()` / `model.undo()` on failure. Castor's writers
never call `end_transaction()`; saving is a separate explicit `save()`.

## Property Operations
- SET: prop.NominalValue = model.create_entity("IfcLabel", "EI120")
- ADD to pset: ifcopenshell.api.run("pset.edit_pset", model, pset=pset, properties={...})
- ADD pset: ifcopenshell.api.run("pset.add_pset", model, product=entity, name="Pset_X")
- REMOVE: properties={"PropName": None}

## Entity Operations (Tier 3)

Prefer the pre-coded `Tier3Writer` (`ifc_processor/services/tier3_writer.py`)
over generating these calls — it is tested and already handles the traps below.

- Create: `root.create_entity(model, ifc_class="IfcSpace", name="X")` — mints
  GlobalId + OwnerHistory. It has **no** LongName/Description parameters;
  set those afterwards with `attribute.edit_attributes(product=e, attributes={...})`.
- Delete an element/space: `root.remove_product(model, product=entity)`.
- **Delete an `IfcZone`/`IfcGroup`: use `group.remove_group(model, group=entity)`.**
  A zone is an `IfcGroup`, NOT an `IfcProduct` — `root.remove_product` is a
  silent no-op for it. Always verify the entity is really gone afterwards.
- Parent a new `IfcSpace` under a storey: `aggregate.assign_object(model,
  products=[space], relating_object=storey)` — a space **decomposes** a
  storey (IfcRelAggregates); it is not "contained in" it.
- Put elements into a zone: `group.assign_group(model, products=[...], group=zone)`.
- Move an element between storeys: `spatial.assign_container(model,
  products=[el], relating_structure=storey)` — already idempotent and
  self-cleaning, so no explicit unassign first.

## Key Rules
- Generated code: never call model.write() or ifcopenshell.open()
- Property name matching is always case-insensitive
- Record every change in the changes list — Git traceability AND the DB
  resync both key off it; an unreported change leaves the index stale
- Type coercion: IfcBoolean for bool, IfcReal for float, IfcLabel for string
- Geometry is out of scope: never author or move physical elements

---

