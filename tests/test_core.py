"""
tests/test_core.py — Comprehensive test suite for the IF Panel Designer core modules.
"""

import os
import sys
import tempfile

import pytest

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ─── Model Tests ─────────────────────────────────────────────────────────────

class TestModels:
    def test_fluorophore_creation(self):
        from core.models import Fluorophore
        f = Fluorophore(name="FITC", ex_peak=490, em_peak=525, laser_lines=[488])
        assert f.name == "FITC"
        assert f.stokes_shift == 35

    def test_primary_antibody_display_name(self):
        from core.models import PrimaryAntibody, HostSpecies
        ab = PrimaryAntibody(
            target="CD3", host_species=HostSpecies.MOUSE,
            clone_name="OKT3", conjugate="AF647",
        )
        assert "CD3" in ab.display_name
        assert ab.is_conjugated is True

    def test_primary_antibody_unconjugated(self):
        from core.models import PrimaryAntibody
        ab = PrimaryAntibody(target="Ki67", conjugate="Unconjugated")
        assert ab.is_conjugated is False

    def test_inventory_item_low_stock(self):
        from core.models import InventoryItem
        item = InventoryItem(target="CD3", total_volume_ul=5, aliquot_size_ul=10)
        assert item.is_low_stock is True

    def test_inventory_item_not_low_stock(self):
        from core.models import InventoryItem
        item = InventoryItem(target="CD3", total_volume_ul=100, aliquot_size_ul=10)
        assert item.is_low_stock is False

    def test_inventory_item_expiry(self):
        from core.models import InventoryItem
        from datetime import date, timedelta
        item = InventoryItem(target="CD3", expiry_date=date.today() + timedelta(days=30))
        assert item.is_expiring_soon is True
        assert item.is_expired is False

    def test_inventory_item_expired(self):
        from core.models import InventoryItem
        from datetime import date, timedelta
        item = InventoryItem(target="CD3", expiry_date=date.today() - timedelta(days=1))
        assert item.is_expired is True

    def test_channel_filter_bounds(self):
        from core.models import Channel
        ch = Channel(name="FITC", laser_nm=488, filter_center=530, filter_bandwidth=30)
        assert ch.filter_low == 515
        assert ch.filter_high == 545
        assert ch.filter_notation == "530/30"

    def test_panel_creation(self):
        from core.models import Panel, PanelTarget, ExperimentType
        panel = Panel(
            name="Test Panel",
            experiment_type=ExperimentType.ICC,
            targets=[PanelTarget(target_name="CD3"), PanelTarget(target_name="Ki67")],
        )
        assert len(panel.targets) == 2
        assert panel.name == "Test Panel"

    def test_instrument_profile_lasers(self):
        from core.models import InstrumentProfile, Channel
        profile = InstrumentProfile(
            name="Test",
            channels=[
                Channel(name="DAPI", laser_nm=405, filter_center=450, filter_bandwidth=50),
                Channel(name="FITC", laser_nm=488, filter_center=530, filter_bandwidth=30),
            ],
        )
        assert sorted(profile.available_laser_wavelengths) == [405, 488]


# ─── Fluorophore Tests ───────────────────────────────────────────────────────

class TestFluorophores:
    def test_load_fluorophores(self):
        from core.fluorophores import load_fluorophores, reload_fluorophores
        reload_fluorophores()
        lib = load_fluorophores()
        assert len(lib) > 20
        assert "FITC" in lib
        assert "Alexa Fluor 647" in lib

    def test_fluorophore_has_spectrum(self):
        from core.fluorophores import load_fluorophores, reload_fluorophores
        reload_fluorophores()
        lib = load_fluorophores()
        fitc = lib["FITC"]
        assert len(fitc.emission_spectrum) > 0

    def test_get_fluorophore(self):
        from core.fluorophores import get_fluorophore, reload_fluorophores
        reload_fluorophores()
        f = get_fluorophore("DAPI")
        assert f is not None
        assert f.ex_peak == 360

    def test_get_fluorophore_not_found(self):
        from core.fluorophores import get_fluorophore, reload_fluorophores
        reload_fluorophores()
        f = get_fluorophore("NonexistentDye")
        assert f is None

    def test_brightness_score(self):
        from core.fluorophores import brightness_score, reload_fluorophores, get_fluorophore
        reload_fluorophores()
        pe = get_fluorophore("PE")
        assert pe is not None
        assert brightness_score(pe) == 5  # Very High

        fitc = get_fluorophore("FITC")
        assert fitc is not None
        assert brightness_score(fitc) == 3  # Moderate

    def test_spectral_overlap_self(self):
        from core.fluorophores import compute_spectral_overlap, get_fluorophore, reload_fluorophores
        reload_fluorophores()
        fitc = get_fluorophore("FITC")
        overlap = compute_spectral_overlap(fitc, fitc)
        assert overlap == pytest.approx(1.0, abs=0.01)

    def test_spectral_overlap_distant(self):
        from core.fluorophores import compute_spectral_overlap, get_fluorophore, reload_fluorophores
        reload_fluorophores()
        dapi = get_fluorophore("DAPI")
        cy5 = get_fluorophore("Cy5")
        overlap = compute_spectral_overlap(dapi, cy5)
        assert overlap < 0.05  # Very low overlap

    def test_spillover_calculation(self):
        from core.fluorophores import compute_spillover, get_fluorophore, reload_fluorophores
        from core.models import Channel
        reload_fluorophores()
        fitc = get_fluorophore("FITC")
        # FITC spillover into its own channel should be highest
        fitc_channel = Channel(name="FITC", laser_nm=488, filter_center=530, filter_bandwidth=30)
        spill = compute_spillover(fitc, fitc_channel)
        assert spill > 0.1  # Should capture significant emission

    def test_get_fluorophores_for_channel(self):
        from core.fluorophores import get_fluorophores_for_channel, reload_fluorophores
        from core.models import Channel
        reload_fluorophores()
        ch = Channel(name="FITC", laser_nm=488, filter_center=530, filter_bandwidth=30)
        fluors = get_fluorophores_for_channel(ch)
        names = [f.name for f in fluors]
        assert "FITC" in names or "Alexa Fluor 488" in names


# ─── Rules Engine Tests ──────────────────────────────────────────────────────

class TestRulesEngine:
    def _make_profile(self):
        from core.models import InstrumentProfile, Channel
        return InstrumentProfile(
            name="Test Microscope",
            channels=[
                Channel(name="DAPI", laser_nm=405, filter_center=450, filter_bandwidth=50),
                Channel(name="FITC", laser_nm=488, filter_center=530, filter_bandwidth=30),
                Channel(name="TRITC", laser_nm=561, filter_center=580, filter_bandwidth=30),
                Channel(name="CY5", laser_nm=640, filter_center=670, filter_bandwidth=30),
            ],
        )

    def test_valid_panel_passes(self):
        from core.models import Panel, PanelTarget, ExperimentType, PrimaryAntibody, HostSpecies
        from core.rules_engine import validate_panel, has_errors
        from core.fluorophores import reload_fluorophores
        reload_fluorophores()

        profile = self._make_profile()
        panel = Panel(
            name="Good Panel",
            experiment_type=ExperimentType.ICC,
            instrument_profile=profile,
            targets=[
                PanelTarget(
                    target_name="CD3",
                    primary=PrimaryAntibody(target="CD3", host_species=HostSpecies.RABBIT, conjugate="Alexa Fluor 488"),
                    fluorophore="Alexa Fluor 488",
                    channel="FITC",
                ),
            ],
        )
        results = validate_panel(panel)
        errors = [r for r in results if r.severity.value == "ERROR"]
        assert len(errors) == 0

    def test_r01_same_host_indirect(self):
        from core.models import (
            Panel, PanelTarget, PrimaryAntibody, SecondaryAntibody,
            ExperimentType, HostSpecies,
        )
        from core.rules_engine import validate_panel

        profile = self._make_profile()
        panel = Panel(
            name="R01 Test",
            experiment_type=ExperimentType.ICC,
            instrument_profile=profile,
            targets=[
                PanelTarget(
                    target_name="CD3",
                    primary=PrimaryAntibody(target="CD3", host_species=HostSpecies.MOUSE, conjugate="Unconjugated"),
                    secondary=SecondaryAntibody(target_host=HostSpecies.MOUSE, fluorophore="AF488"),
                ),
                PanelTarget(
                    target_name="Ki67",
                    primary=PrimaryAntibody(target="Ki67", host_species=HostSpecies.MOUSE, conjugate="Unconjugated"),
                    secondary=SecondaryAntibody(target_host=HostSpecies.MOUSE, fluorophore="AF647"),
                ),
            ],
        )
        results = validate_panel(panel)
        r01_results = [r for r in results if r.rule_id == "R01"]
        assert len(r01_results) > 0
        assert r01_results[0].severity.value == "ERROR"

    def test_r04_duplicate_fluorophore(self):
        from core.models import Panel, PanelTarget, ExperimentType
        from core.rules_engine import validate_panel
        from core.fluorophores import reload_fluorophores
        reload_fluorophores()

        profile = self._make_profile()
        panel = Panel(
            name="R04 Test",
            experiment_type=ExperimentType.ICC,
            instrument_profile=profile,
            targets=[
                PanelTarget(target_name="CD3", fluorophore="FITC", channel="FITC"),
                PanelTarget(target_name="Ki67", fluorophore="FITC", channel="FITC"),
            ],
        )
        results = validate_panel(panel)
        r04_results = [r for r in results if r.rule_id == "R04"]
        assert len(r04_results) > 0

    def test_r13_too_many_targets(self):
        from core.models import Panel, PanelTarget, ExperimentType
        from core.rules_engine import validate_panel

        profile = self._make_profile()  # 4 channels, 3 usable (minus DAPI)
        targets = [PanelTarget(target_name=f"Target{i}") for i in range(5)]

        panel = Panel(
            name="R13 Test",
            experiment_type=ExperimentType.ICC,
            instrument_profile=profile,
            targets=targets,
        )
        results = validate_panel(panel)
        r13_results = [r for r in results if r.rule_id == "R13"]
        assert len(r13_results) > 0

    def test_r12_dapi_live_cell(self):
        from core.models import Panel, PanelTarget, ExperimentType
        from core.rules_engine import validate_panel
        from core.fluorophores import reload_fluorophores
        reload_fluorophores()

        profile = self._make_profile()
        panel = Panel(
            name="R12 Test",
            experiment_type=ExperimentType.LIVE_IF,
            instrument_profile=profile,
            targets=[
                PanelTarget(target_name="Nuclear", fluorophore="DAPI", channel="DAPI"),
            ],
        )
        results = validate_panel(panel)
        r12_results = [r for r in results if r.rule_id == "R12"]
        assert len(r12_results) > 0

    def test_severity_ordering(self):
        from core.models import Panel, PanelTarget, ExperimentType
        from core.rules_engine import validate_panel
        from core.fluorophores import reload_fluorophores
        reload_fluorophores()

        profile = self._make_profile()
        panel = Panel(
            name="Ordering Test",
            experiment_type=ExperimentType.ICC,
            instrument_profile=profile,
            targets=[
                PanelTarget(target_name="CD3", fluorophore="FITC", channel="FITC"),
                PanelTarget(target_name="Ki67", fluorophore="FITC", channel="FITC"),  # R04 error
            ],
        )
        results = validate_panel(panel)
        if len(results) > 1:
            # Errors should come first
            severities = [r.severity.value for r in results]
            error_idx = [i for i, s in enumerate(severities) if s == "ERROR"]
            warning_idx = [i for i, s in enumerate(severities) if s == "WARNING"]
            if error_idx and warning_idx:
                assert max(error_idx) < min(warning_idx)


# ─── Cost Calculator Tests ───────────────────────────────────────────────────

class TestCostCalculator:
    def test_parse_dilution(self):
        from core.cost_calculator import parse_dilution
        assert parse_dilution("1:500") == 500
        assert parse_dilution("1/200") == 200
        assert parse_dilution("") is None

    def test_parse_package_size(self):
        from core.cost_calculator import parse_package_size
        assert parse_package_size("100 µg") == 100
        assert parse_package_size("0.1 mg") == 100
        assert parse_package_size("") is None

    def test_volume_calculation(self):
        from core.cost_calculator import calculate_volume_needed
        vol = calculate_volume_needed(num_samples=10, dilution_factor=200, volume_per_sample_ul=100)
        # (100/200) * 10 * 1.2 = 6.0
        assert vol == pytest.approx(6.0, abs=0.1)

    def test_units_to_order(self):
        from core.cost_calculator import calculate_units_to_order
        assert calculate_units_to_order(150, 100) == 2
        assert calculate_units_to_order(100, 100) == 1
        assert calculate_units_to_order(50, 100) == 1


# ─── Database & Inventory Tests ──────────────────────────────────────────────

class TestDatabase:
    def test_database_creation(self):
        from core.database import DatabaseManager
        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(tmpdir)
            db.create_all()
            assert os.path.exists(os.path.join(tmpdir, "inventory.db"))
            assert os.path.exists(os.path.join(tmpdir, "price_cache.db"))

    def test_inventory_crud(self):
        from core.database import DatabaseManager
        from core.inventory import add_item, get_item, get_all_items, delete_item
        from core.models import InventoryItem

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(tmpdir)
            db.create_all()

            item = InventoryItem(
                target="CD3",
                antibody_name="Anti-CD3 Rabbit",
                vendor="Abcam",
                catalog_no="ab135372",
                total_volume_ul=200,
            )

            with db.inventory_session() as session:
                add_item(session, item)

            with db.inventory_session() as session:
                retrieved = get_item(session, item.id)
                assert retrieved is not None
                assert retrieved.target == "CD3"

            with db.inventory_session() as session:
                all_items = get_all_items(session)
                assert len(all_items) == 1

            with db.inventory_session() as session:
                delete_item(session, item.id)

            with db.inventory_session() as session:
                assert get_item(session, item.id) is None

    def test_inventory_search(self):
        from core.database import DatabaseManager
        from core.inventory import add_item, search_items
        from core.models import InventoryItem

        with tempfile.TemporaryDirectory() as tmpdir:
            db = DatabaseManager(tmpdir)
            db.create_all()

            with db.inventory_session() as session:
                add_item(session, InventoryItem(target="CD3", vendor="Abcam"))
                add_item(session, InventoryItem(target="Ki67", vendor="CST"))
                add_item(session, InventoryItem(target="CD8", vendor="Abcam"))

            with db.inventory_session() as session:
                results = search_items(session, "CD")
                assert len(results) == 2  # CD3 and CD8

                results = search_items(session, "Abcam")
                assert len(results) == 2


# ─── Protocol Generator Tests ────────────────────────────────────────────────

class TestProtocolGenerator:
    def test_generate_protocol(self):
        from core.protocol_generator import generate_protocol
        from core.models import Panel, PanelTarget, ExperimentType, InstrumentProfile, Channel

        profile = InstrumentProfile(
            name="Test",
            channels=[
                Channel(name="DAPI", laser_nm=405, filter_center=450, filter_bandwidth=50),
                Channel(name="FITC", laser_nm=488, filter_center=530, filter_bandwidth=30),
            ],
        )
        panel = Panel(
            name="Test Protocol",
            experiment_type=ExperimentType.ICC,
            instrument_profile=profile,
            targets=[PanelTarget(target_name="CD3")],
        )

        protocol = generate_protocol(panel, lab_name="Test Lab", researcher="Dr. Smith")
        assert protocol.title == "Protocol: Test Protocol"
        assert len(protocol.sections) > 5
        assert protocol.author == "Dr. Smith"
        assert len(protocol.controls) > 0


# ─── Exporter Tests ──────────────────────────────────────────────────────────

class TestExporters:
    def _make_protocol(self):
        from core.protocol_generator import generate_protocol
        from core.models import Panel, PanelTarget, ExperimentType, InstrumentProfile, Channel

        profile = InstrumentProfile(
            name="Test",
            channels=[Channel(name="DAPI", laser_nm=405, filter_center=450, filter_bandwidth=50)],
        )
        panel = Panel(
            name="Export Test",
            experiment_type=ExperimentType.ICC,
            instrument_profile=profile,
            targets=[PanelTarget(target_name="CD3")],
        )
        return generate_protocol(panel)

    def test_export_docx(self):
        from core.exporters import export_docx
        protocol = self._make_protocol()
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            result = export_docx(protocol, f.name)
            assert result is True
            assert os.path.getsize(f.name) > 0
            os.unlink(f.name)

    def test_export_pdf(self):
        from core.exporters import export_pdf
        protocol = self._make_protocol()
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            result = export_pdf(protocol, f.name)
            assert result is True
            assert os.path.getsize(f.name) > 0
            os.unlink(f.name)

    def test_export_xlsx(self):
        from core.exporters import export_xlsx
        protocol = self._make_protocol()
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as f:
            result = export_xlsx(protocol, f.name)
            assert result is True
            assert os.path.getsize(f.name) > 0
            os.unlink(f.name)
