# IF Panel Designer

A scientific workflow tool for designing immunofluorescence experiments, managing antibody inventories, pulling live pricing, validating panel compatibility, and generating publication-ready protocols.

## Features

- **Panel Builder** — 6-step wizard to design multi-color IF panels with automatic fluorophore assignment, brightness optimization, and spillover minimization
- **Antibody Inventory** — Full CRUD management of lab antibody stocks with low-stock alerts, expiry tracking, freeze-thaw counting, and CSV import/export
- **Rules Engine** — 15 scientific validation rules enforcing proper panel design (host species conflicts, spectral overlap, brightness matching, etc.)
- **Spectral Analysis** — Real-time spillover matrix calculation and heatmap visualization
- **Price Search** — Web scraping from Biocompare, Abcam, CST, BioLegend, and other major vendors with 24-hour cache
- **Protocol Generator** — Auto-generates complete experimental protocols with reagent tables, controls, and troubleshooting guides
- **Multi-format Export** — PDF, DOCX, and Excel export of protocols and reagent lists
- **Alternative Plans** — Side-by-side comparison of multiple panel designs scored on cost, validation, and spectral overlap

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize databases
python -c "
from core.database import DatabaseManager
db = DatabaseManager('db')
db.create_all()
print('Databases initialized.')
"

# Run the Streamlit GUI
streamlit run gui_streamlit/app.py
```

## Project Structure

```
if_panel_designer/
├── core/                    # ALL business logic (GUI-independent)
│   ├── models.py            # Pydantic data models
│   ├── database.py          # SQLAlchemy ORM + session management
│   ├── inventory.py         # Inventory CRUD operations
│   ├── fluorophores.py      # Fluorophore library + spectral calculations
│   ├── rules_engine.py      # 15 panel validation rules
│   ├── panel_builder.py     # Fluorophore assignment optimizer
│   ├── scraper.py           # Web pricing scraper (Biocompare et al.)
│   ├── price_cache.py       # SQLite TTL cache for prices
│   ├── protocol_generator.py # Protocol assembly engine
│   ├── cost_calculator.py   # Volume & cost formulas
│   ├── alternative_plans.py # Alternative plan generator
│   └── exporters.py         # PDF, DOCX, Excel export
├── data/                    # Static reference data
│   ├── fluorophores.yaml    # 40+ fluorophore spectral database
│   ├── instrument_profiles.yaml
│   └── troubleshooting.yaml
├── db/                      # Runtime SQLite databases
├── gui_streamlit/           # Phase 1: Streamlit GUI
│   ├── app.py               # Entry point + navigation
│   └── pages/               # Dashboard, Inventory, Panel Builder, etc.
├── gui_pyside6/             # Phase 2: Desktop GUI (future)
├── tests/                   # pytest test suite
├── config.yaml              # Lab settings
└── requirements.txt
```

## Architecture

All business logic lives in `core/` as pure Python modules — completely independent of any GUI framework. This enables:
- **Streamlit GUI** (Phase 1) — rapid prototyping, browser-based
- **PySide6 Desktop GUI** (Phase 2) — production desktop app with identical logic

The two GUI layers import from `core/` only and never from each other.

## Running Tests

```bash
cd if_panel_designer
python -m pytest tests/ -v
```

## Panel Design Workflow

1. **Setup** — Define experiment type, sample species, instrument profile
2. **Targets** — Add target proteins with expression levels
3. **Antibodies** — Select from inventory or search catalogs
4. **Channels** — Assign fluorophores to detector channels (auto or manual)
5. **Validation** — Rules engine checks for errors and warnings
6. **Alternatives** — Compare Plan A/B/C side by side

## Validation Rules

| Rule | Description | Severity |
|------|-------------|----------|
| R01 | Same-host indirect primaries | ERROR |
| R02 | Fluorophore not excitable by available laser | ERROR |
| R03 | Emission outside all detector channels | ERROR |
| R04 | Duplicate fluorophore on two targets | ERROR |
| R05 | Tandem dye with fixation protocol | WARNING |
| R06 | Mixed polymer dye manufacturers | WARNING |
| R07 | Spillover >15% between channels | WARNING |
| R08 | Dim fluorophore on rare target | WARNING |
| R09 | Antibody not validated for application | WARNING |
| R10 | Secondary host = sample species | ERROR |
| R11 | Fixation-only antibody in live experiment | WARNING |
| R12 | DAPI in live-cell experiment | WARNING |
| R13 | More targets than available channels | ERROR |
| R14 | No viability dye in flow panel | WARNING |
| R15 | Missing isotype controls | INFO |

## Configuration

Edit `config.yaml` to set:
- Lab name, PI, institution
- Default instrument profile
- Scraping vendor priority and timeout
- Inventory alert thresholds
