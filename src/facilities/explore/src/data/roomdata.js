// roomdata.js — catalog of Facility/Schedule tables "prepared in Castor".
//
// Each table has display `columns` and `rows`. Every row carries the identifier
// fields a room can be matched on (globalId + IFC properties like number,
// department, building). The user picks a table, then a FILTER KEY (any of those
// identifiers); rows are shown where row[key] === the room's value for that key.
// Filtering by `department` or `building` therefore shows the whole group, not a
// single room. In production Castor replaces this catalog via SET_TABLE_CATALOG.

const B = "Palác Jiráskovo";

let CATALOG = {
  workorders: {
    group: "Facility", label: "Work orders",
    columns: [{ field: "wo", label: "Work order" }, { field: "status", label: "Status" }, { field: "due", label: "Due" }],
    rows: [
      { wo: "WO-1042 · HVAC filter", status: "Open", due: "30 Jun", _status: "amber", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { wo: "WO-0998 · Lighting fix", status: "Done", due: "—", _status: "green", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { wo: "WO-1099 · Server cooling", status: "Open", due: "20 Jul", _status: "red", globalId: "3cD9xR$E1B0xfa4C", number: "4C", department: "TechSpace", building: B },
      { wo: "WO-1071 · Repaint", status: "Open", due: "15 Jul", _status: "amber", globalId: "1aB7zT$E1B0xfa4A", number: "4A", department: "—", building: B },
    ],
  },
  assets: {
    group: "Facility", label: "Assets",
    columns: [{ field: "asset", label: "Asset" }, { field: "status", label: "Status" }, { field: "due", label: "Due" }],
    rows: [
      { asset: "HVAC H-122 · Daikin", status: "OK", due: "+44 d", _status: "green", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { asset: "Radiator RAD-4B", status: "OK", due: "—", _status: "green", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { asset: "Server rack SR-4C", status: "OK", due: "—", _status: "green", globalId: "3cD9xR$E1B0xfa4C", number: "4C", department: "TechSpace", building: B },
      { asset: "AC unit A-4A", status: "Service due", due: "−3 d", _status: "red", globalId: "1aB7zT$E1B0xfa4A", number: "4A", department: "—", building: B },
    ],
  },
  sensors: {
    group: "Facility", label: "Sensors",
    columns: [{ field: "sensor", label: "Sensor" }, { field: "reading", label: "Reading" }, { field: "status", label: "Status" }],
    rows: [
      { sensor: "Temp T-4B", reading: "22.4 °C", status: "OK", _status: "green", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { sensor: "CO₂ C-4B", reading: "620 ppm", status: "OK", _status: "green", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { sensor: "Temp T-4C", reading: "24.9 °C", status: "High", _status: "amber", globalId: "3cD9xR$E1B0xfa4C", number: "4C", department: "TechSpace", building: B },
    ],
  },
  costs: {
    group: "Facility", label: "Costs",
    columns: [{ field: "item", label: "Item" }, { field: "amount", label: "Amount" }],
    rows: [
      { item: "Maintenance YTD", amount: "42 800 Kč", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { item: "Energy YTD", amount: "18 200 Kč", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { item: "Server room power", amount: "31 400 Kč", globalId: "3cD9xR$E1B0xfa4C", number: "4C", department: "TechSpace", building: B },
    ],
  },
  schedule: {
    group: "Schedule", label: "Maintenance schedule",
    columns: [{ field: "task", label: "Task" }, { field: "freq", label: "Frequency" }, { field: "next", label: "Next" }],
    rows: [
      { task: "HVAC service", freq: "Quarterly", next: "08/2026", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { task: "Fire-safety check", freq: "Yearly", next: "03/2027", globalId: "2nQ8aF$E1B0xfa4B", number: "4B", department: "TechSpace", building: B },
      { task: "Cat-A inspection", freq: "Half-yearly", next: "09/2026", globalId: "1aB7zT$E1B0xfa4A", number: "4A", department: "—", building: B },
    ],
  },
};

// Host replaces/extends the catalog (SET_TABLE_CATALOG).
export function setTableCatalog(tables) {
  if (tables && typeof tables === "object") CATALOG = Object.assign({}, CATALOG, tables);
}
export function tableCatalog() {
  return CATALOG;
}

// Rows of `tableKey` whose `filterKey` field equals the room's value for that key.
// keyValues = { globalId, ...room props }. filterKey is any of those identifiers.
export function filterRows(tableKey, filterKey, keyValues) {
  const t = CATALOG[tableKey];
  if (!t) return { columns: [], rows: [] };
  const val = keyValues[filterKey];
  const rows = (val !== undefined && val !== "") ? t.rows.filter((r) => String(r[filterKey]) === String(val)) : [];
  return { columns: t.columns, rows };
}
