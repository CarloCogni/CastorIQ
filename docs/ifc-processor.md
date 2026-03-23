# IFC Processor

The IFC Processor is Castor's foundation layer. It ingests IFC files, validates them, extracts structured entity data, and generates semantic descriptions that feed the RAG pipeline and write-back system.

## Supported Schemas

IfcOpenShell (the IFC parsing engine used by Castor) supports three official schema families:

| Schema | ISO Publication | Status | IfcOpenShell |
|--------|----------------|--------|--------------|
| **IFC2X3 TC1** | ISO/PAS 16739:2005 | Official | Full parse + geometry |
| **IFC4 Add2 TC1** | ISO 16739-1:2018 | Official | Full parse + geometry ← **Castor target** |
| **IFC4X3 Add2** | ISO 16739-1:2024 | Official (latest) | Full parse (geometry expanding) |

**Castor's pipeline targets IFC4 (IFC4 Add2 TC1)** as the canonical processing schema.
Files uploaded in IFC2X3 (or any other legacy schema) are stored but not indexed.
The upload flow detects the schema from the file header before parsing and offers an
in-app conversion to IFC4 via `IFCSchemaConverterService`.

> Note: "IFC4" in IfcOpenShell maps to IFC4 Add2 TC1, not the retired plain IFC4 (ISO 16739:2013).
> IFC4X3 support is included but Castor does not yet use it as a pipeline target.

## Validation

IFC files go through two validation checks before parsing:

1. **Extension check** — file must have `.ifc` extension
2. **Content check** — file must contain a valid STEP header (`ISO-10303-21`), preventing renamed non-IFC files from entering the system

Additionally, duplicate detection compares file hashes to prevent re-uploading identical files to the same project.

## Parsing Pipeline
```
Upload → Validate (extension + STEP header) → Parse (IfcOpenShell)
       → Extract Spatial Hierarchy
       → Extract Entities + Properties
       → Generate Semantic Descriptions
       → Store in Database
```

### Spatial Hierarchy Extraction

The parser traverses the IFC spatial structure:
```
IfcProject
  └── IfcSite
        └── IfcBuilding
              ├── IfcBuildingStorey (Level 0)
              │     ├── IfcWall, IfcDoor, IfcWindow, ...
              │     └── IfcSpace
              ├── IfcBuildingStorey (Level 1)
              │     └── ...
              └── ...
```

Each entity is tagged with its containing storey, enabling floor-level filtering in queries and modifications.

### Entity Extraction

For each building element, the processor extracts:

| Field | Source | Example |
|---|---|---|
| `global_id` | `entity.GlobalId` | `"2O2Fr$t4X7Zf8NOew3FNr2"` |
| `ifc_type` | `entity.is_a()` | `"IfcWallStandardCase"` |
| `name` | `entity.Name` | `"W-01"` |
| `description` | `entity.Description` | `"External wall"` |
| `object_type` | `entity.ObjectType` | `"Basic Wall:Generic - 200mm"` |
| `tag` | `entity.Tag` | `"W-01"` |
| `building_storey` | Spatial containment lookup | `"Level 1"` |
| `properties` | All PropertySets as JSON | `{"Pset_WallCommon": {"IsExternal": true, ...}}` |

### Property Extraction

Properties are extracted from all PropertySets (`IfcPropertySet`) associated with each entity via `IfcRelDefinesByProperties`. The result is a nested JSON structure:
```json
{
  "Pset_WallCommon": {
    "IsExternal": true,
    "IsLoadBearing": false,
    "FireRating": "EI60",
    "ThermalTransmittance": 0.25
  },
  "Pset_QuantityTakeOff": {
    "Reference": "Basic Wall:Generic - 200mm"
  }
}
```

This JSON is stored in the `IFCEntity.properties` JSONField, making it queryable from Django ORM and accessible to the write-back validator without re-opening the IFC file.

## Semantic Description Generation

After extraction, each entity gets a human-readable description optimised for embedding similarity. This is the text that enters the RAG vector space.

**Template pattern:**
```
{ifc_type} "{name}" on {storey}.
Type: {object_type}.
Properties: {key properties as natural language}.
```

**Example output:**
```
IfcWallStandardCase "W-01" on Level 1.
Type: Basic Wall:Generic - 200mm.
External wall. Load-bearing: no. Fire rating: EI60.
Thermal transmittance: 0.25 W/m²K.
```

The description intentionally uses natural language rather than raw JSON so that user queries like "fire-rated walls on the ground floor" have high semantic similarity to the stored descriptions.

## Data Model

### IFCFile

Represents an uploaded IFC file within a project.

- Links to a `Project` (environment)
- Tracks processing status: `pending` → `processing` → `completed` / `failed`
- Stores file hash for duplicate detection
- Records entity count after successful parsing

### IFCEntity

Represents a single extracted building element.

- Links to its parent `IFCFile`
- `global_id` — the IFC GlobalId, unique within the file
- `ifc_type` — entity class (IfcWall, IfcDoor, etc.)
- `name`, `description`, `object_type`, `tag` — direct IFC attributes
- `building_storey` — resolved spatial container
- `properties` — JSONField with all PropertySet data
- `embedding` — VectorField (1024d) for RAG similarity search

## What Gets Stored vs. What Stays in the File

| Data | Stored in DB | Stays in IFC file |
|---|---|---|
| Entity attributes (name, type, GlobalId) | ✅ | ✅ |
| Properties (all PropertySets) | ✅ (JSON) | ✅ |
| Spatial hierarchy (storey assignment) | ✅ | ✅ |
| Semantic description | ✅ | — |
| Embedding vector | ✅ | — |
| Geometry | — | ✅ |
| Material associations | — (future) | ✅ |
| Relationships (aggregates, connections) | — (future) | ✅ |

The DB acts as a queryable index. The IFC file remains the source of truth. Write-back operations modify the IFC file directly (via IfcOpenShell), then the DB is re-synced.

## Management Commands
```bash
# Parse all uploaded IFC files with "pending" status
uv run manage.py parse_ifc --all-pending

# Parse a specific file by ID
uv run manage.py parse_ifc --file-id <uuid>
```

## Design Decisions

1. **IfcOpenShell for both read and write** — single library for the full lifecycle, avoiding translation between tools.
2. **Flat property JSON** — properties stored as a single JSONField rather than normalised tables. Simpler to query, display, and pass to the LLM as context. Trade-off: no relational queries on individual properties (mitigated by `property_match` filter in write-back).
3. **Semantic descriptions over raw data** — embedding natural language descriptions rather than structured JSON dramatically improves retrieval quality for natural language queries.
4. **Storey as a string field** — stored as the storey name (e.g. "Level 1") rather than a FK. Simpler, and IFC storey names are stable within a file. Enables case-insensitive contains filtering.