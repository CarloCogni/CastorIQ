# docs/fived/OPENBIM_UX_1_LANGUAGE_NEUTRALITY_SPEC.md

# OPENBIM-UX-1 — Language & UX Neutrality Spec

**Status:** Local implementation guide (copy/grouping only)  
**Related:** SEM-4 / SEM-4A semantic sources; 5D-UX-1 quantity prep hierarchy  

---

## A. Product rule

Castor is **IFC / openBIM-first**.

Authoring-tool properties (Family, Type, Category, Workset, Identity Data, OmniClass, Assembly Code, etc.) may appear because they are **included in the IFC export**. Castor must **not** assume Revit or any specific authoring tool as the platform default.

Do **not** remove these fields when present. Treat them as **exported instance evidence**, labeled so founders and users never read Castor as Revit-only.

---

## B. Preferred labels

### Use

- IFC evidence / IFC-native evidence  
- Exported authoring properties / Source-tool properties  
- IFC Classification Reference  
- Authoring classification properties (for OmniClass / Assembly–style exports)  
- Castor schema mapping  
- User semantic source profile  
- Missing in this IFC export  

### Avoid

- Revit Family / Revit Type / Revit Category  
- Unsupported zone  
- Auto classification  
- “Model understands everything”  
- Cost-ready / BOQ-ready  

---

## C. Grouping rules

### IFC-native evidence

- IFC Classification Reference (`IfcRelAssociatesClassification` / ClassRef.*)  
- Spatial Storey / IFC spatial structure  
- IFC Class (`IfcWall`, `IfcBeam`, …)  
- QTO / Quantity Sets  

### Exported authoring properties

- Category, Family, Type, Workset  
- Identity Data  
- OmniClass, Assembly Code (and similar authoring classification props)  

### Castor mapping

- Classification target (`classification_code`)  
- Package target  
- Work Package target  

### User semantic source profile

User chooses which evidence feeds Level / Zone / Classification / Package / Unit readiness — independent of Castor schema mapping values.

---

## D. Help copy (required themes)

1. Some fields are exported by the authoring tool into the IFC file (Family, Type, Category, Workset, OmniClass, Assembly Code, …). Castor treats them as model evidence, **not** as Revit-only assumptions. Availability varies by export.

2. IFC Classification References are **native IFC relationships**. They are separate from authoring properties and separate from Castor schema mapping.

3. Zone is **not found in this IFC export** (when missing). Another export may include zone membership, or the user may choose a zone source from available properties.

---

## E. Acceptance criteria

- UI does not imply Castor is Revit-only.  
- Family/Type/Category remain available, grouped/worded as exported authoring properties.  
- IFC Classification Reference remains separate from OmniClass/Assembly.  
- Zone absence says missing in this IFC export.  
- Castor schema mapping remains separate from model evidence.  
- No feature regression; no backend/data/query changes; no push from this pass.  

---

## F. Implementation scope

Allowed: headings, helper text, tab labels, table header wording, help modal, chips/group labels, template-only CSS if needed for grouping cues.  

Not allowed: remove fields/filters; change query params; parser/backend; mapping/unit/freeze/snapshot logic; migrations.
