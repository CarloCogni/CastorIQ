# writeback/services/api_sheet.py
"""The ifcopenshell.api sheet the code generator sees (spec G-2).

Hand-written and reviewed like code: signatures and one-line docstrings for
the modules a modification can need, no worked examples, because examples get
copied. ``test_api_sheet`` resolves every ``module.function`` below against the
installed ifcopenshell and checks every keyword it names, so the sheet cannot
drift from the pinned version silently. The notation is the calling convention:
the sandbox binds every module named here with the model implied
(``code_sandbox.API_MODULES``), so a call copied from the sheet runs as written.
"""

API_SHEET = """\
Call each function as written; the model is implied: spatial.assign_container(products=[e], relating_structure=storey).
The long form ifcopenshell.api.run("module.function", model, **kwargs) also works. Modules and keywords:

pset.add_pset(product=entity, name=str) -> IfcPropertySet — the pset by name: returns the existing one (which may be shared with other entities) or creates it empty
pset.unshare_pset(products=[entities], pset=pset) -> [IfcPropertySet] — give these entities their own copy of a shared pset; raises when it is not shared
pset.edit_pset(pset=pset, properties=dict) — set values; a value of None removes that property
pset.remove_pset(product=entity, pset=pset) — detach and delete a pset from one entity
root.create_entity(ifc_class=str, name=str, predefined_type=str) -> entity — new rooted entity with GlobalId (never model.create_entity)
root.remove_product(product=entity) — delete an element or space and its relationships
root.copy_class(product=entity) -> entity — deep copy of one entity with a fresh GlobalId
spatial.assign_container(products=[entities], relating_structure=storey_or_space) — place elements in a storey/space
spatial.unassign_container(products=[entities]) — remove elements from their container
aggregate.assign_object(products=[entities], relating_object=parent) — nest under a parent (space under storey)
aggregate.unassign_object(products=[entities]) — remove from the parent
type.assign_type(related_objects=[entities], relating_type=type_entity) — set the type object of elements
type.unassign_type(related_objects=[entities]) — remove the type object
attribute.edit_attributes(product=entity, attributes=dict) — set Name, Description, ObjectType, Tag, LongName
classification.add_classification(classification=str) -> IfcClassification — find-or-create a system by name
classification.add_reference(products=[entities], identification=str, name=str, classification=system) — classify
classification.remove_reference(reference=ref, products=[entities]) — unclassify
material.add_material(name=str, category=str) -> IfcMaterial — create a material
material.assign_material(products=[entities], type="IfcMaterial", material=material) — assign a material
material.unassign_material(products=[entities]) — remove the material assignment
group.add_group(name=str) -> IfcGroup — create a plain group; an IfcZone is root.create_entity(ifc_class="IfcZone", name=...)
group.assign_group(products=[entities], group=group) — add elements to a group
group.unassign_group(products=[entities], group=group) — remove elements from a group
group.remove_group(group=group) — delete a group (an IfcZone is a group, not a product)

Reading (ifcopenshell.util.element as element): element.get_pset(entity, "Pset", "Prop") -> value;
element.get_psets(entity) -> {pset: {prop: value}}; element.get_container(entity) -> storey or space;
element.get_decomposition(entity) -> set; element.get_type(entity) -> type object;
element.get_elements_by_pset(pset) -> set of the entities a pset is attached to; model.by_guid(gid);
model.by_type("IfcWall") includes subclasses. Storeys and spaces by name: compare .Name on model.by_type(...).
"""
