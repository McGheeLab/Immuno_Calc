"""
core/models.py — Pydantic v2 data models for the IF Panel Design Software.

All data structures used across the application. These models are GUI-agnostic
and serve as the contract between all modules.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ─── Enums ───────────────────────────────────────────────────────────────────

class HostSpecies(str, Enum):
    MOUSE = "Mouse"
    RABBIT = "Rabbit"
    GOAT = "Goat"
    RAT = "Rat"
    CHICKEN = "Chicken"
    DONKEY = "Donkey"
    SHEEP = "Sheep"
    HAMSTER = "Hamster"
    GUINEA_PIG = "Guinea Pig"
    HUMAN = "Human"
    OTHER = "Other"


class Clonality(str, Enum):
    MONOCLONAL = "Monoclonal"
    POLYCLONAL = "Polyclonal"
    OLIGOCLONAL = "Oligoclonal"


class ExperimentType(str, Enum):
    ICC = "ICC"
    IHC_IF = "IHC-IF"
    FLOW_CYTOMETRY = "Flow Cytometry"
    LIVE_IF = "Live IF"
    MULTIPLEX_IF = "Multiplex IF"
    PLA = "PLA"


class SampleType(str, Enum):
    CULTURED_CELLS = "Cultured cells"
    FFPE_TISSUE = "FFPE tissue"
    CRYOSECTION = "Cryosection"
    SUSPENSION_CELLS = "Suspension cells"
    ORGANOIDS = "Organoids"
    BLOOD_SMEAR = "Blood smear"
    TMA = "TMA"
    WHOLE_MOUNT = "Whole mount"
    CYTOSPIN = "Cytospin"


class SampleSpecies(str, Enum):
    HUMAN = "Human"
    MOUSE = "Mouse"
    RAT = "Rat"
    RABBIT = "Rabbit"
    ZEBRAFISH = "Zebrafish"
    OTHER = "Other"


class ExpressionLevel(str, Enum):
    RARE = "Rare"
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class SubcellularLocation(str, Enum):
    NUCLEAR = "Nuclear"
    CYTOPLASMIC = "Cytoplasmic"
    MEMBRANE = "Membrane"
    SECRETED = "Secreted"
    EXTRACELLULAR = "Extracellular"


class StorageTemp(str, Enum):
    MINUS_80 = "-80°C"
    MINUS_20 = "-20°C"
    FOUR_C = "4°C"
    RT = "RT"


class Severity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


class InstrumentMode(str, Enum):
    CONVENTIONAL = "conventional"
    SPECTRAL = "spectral"


class ExportFormat(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    XLSX = "xlsx"


class AntigenRetrievalMethod(str, Enum):
    HIER_CITRATE_PH6 = "HIER Citrate pH 6.0"
    HIER_EDTA_PH9 = "HIER EDTA/Tris pH 9.0"
    ENZYMATIC_PROTEINASE_K = "Enzymatic Proteinase K"
    ENZYMATIC_TRYPSIN = "Enzymatic Trypsin"
    NONE = "None"


class FragmentType(str, Enum):
    FULL_IGG = "Full IgG"
    FAB_PRIME_2 = "F(ab')2"
    FAB = "Fab"


# ─── Core Scientific Models ─────────────────────────────────────────────────

class Fluorophore(BaseModel):
    """A fluorescent molecule with spectral properties."""
    name: str
    ex_peak: float = Field(description="Excitation peak in nm")
    em_peak: float = Field(description="Emission peak in nm")
    brightness: str = Field(default="Moderate", description="Brightness rating")
    laser_lines: list[float] = Field(default_factory=list, description="Compatible laser wavelengths in nm")
    emission_spectrum: list[float] = Field(
        default_factory=list,
        description="Emission intensity array sampled every 5nm from 400-900nm"
    )
    stokes_shift: float = Field(default=0, description="Difference between em and ex peaks")
    is_tandem: bool = Field(default=False, description="Whether this is a tandem/FRET dye")
    is_polymer: bool = Field(default=False, description="Whether this is a polymer dye (BV/BB/BD Horizon)")
    manufacturer_family: str = Field(default="", description="e.g. 'BioLegend BV', 'BD Horizon'")
    notes: str = Field(default="")

    def model_post_init(self, __context):
        if self.stokes_shift == 0 and self.ex_peak and self.em_peak:
            self.stokes_shift = self.em_peak - self.ex_peak


class Laser(BaseModel):
    """A laser line on an instrument."""
    wavelength_nm: float
    power_mw: float = Field(default=0)
    name: str = Field(default="")
    color: str = Field(default="")


class Channel(BaseModel):
    """A detector channel on an instrument."""
    name: str
    laser_nm: float
    filter_center: float = Field(description="Center wavelength of bandpass filter in nm")
    filter_bandwidth: float = Field(description="Bandwidth of bandpass filter in nm")
    common_fluorophores: list[str] = Field(default_factory=list)

    @property
    def filter_low(self) -> float:
        return self.filter_center - self.filter_bandwidth / 2

    @property
    def filter_high(self) -> float:
        return self.filter_center + self.filter_bandwidth / 2

    @property
    def filter_notation(self) -> str:
        return f"{int(self.filter_center)}/{int(self.filter_bandwidth)}"


class InstrumentProfile(BaseModel):
    """Complete instrument configuration."""
    name: str
    channels: list[Channel] = Field(default_factory=list)
    lasers: list[Laser] = Field(default_factory=list)
    mode: InstrumentMode = Field(default=InstrumentMode.CONVENTIONAL)

    @property
    def available_laser_wavelengths(self) -> list[float]:
        return sorted(set(ch.laser_nm for ch in self.channels))


# ─── Antibody Models ────────────────────────────────────────────────────────

class PrimaryAntibody(BaseModel):
    """A primary antibody with full attributes."""
    target: str = Field(description="Target antigen, e.g. CD3, Ki67, GFAP")
    host_species: HostSpecies = Field(default=HostSpecies.RABBIT)
    isotype: str = Field(default="IgG")
    clonality: Clonality = Field(default=Clonality.MONOCLONAL)
    clone_name: str = Field(default="")
    conjugate: str = Field(default="Unconjugated", description="Fluorophore or 'Unconjugated'")
    validated_applications: list[str] = Field(default_factory=list)
    reactivity: list[str] = Field(default_factory=list, description="Reactive species list")
    dilution: str = Field(default="", description="Recommended working dilution")
    concentration: float = Field(default=0, description="mg/mL")
    catalog_no: str = Field(default="")
    vendor: str = Field(default="")
    lot_no: str = Field(default="")
    rrid: str = Field(default="", description="Research Resource Identifier")
    storage_conditions: str = Field(default="")
    notes: str = Field(default="")

    @property
    def is_conjugated(self) -> bool:
        return self.conjugate.lower() not in ("unconjugated", "", "none")

    @property
    def display_name(self) -> str:
        parts = [f"Anti-{self.target}", f"({self.host_species.value}"]
        if self.clone_name:
            parts.append(f", {self.clone_name}")
        parts.append(")")
        if self.is_conjugated:
            parts.append(f" [{self.conjugate}]")
        return "".join(parts)


class SecondaryAntibody(BaseModel):
    """A secondary antibody."""
    target_host: HostSpecies = Field(description="Host species of the primary it recognizes")
    isotype_specificity: str = Field(default="IgG (H+L)", description="e.g. IgG, IgG1, IgG2a")
    fragment_type: FragmentType = Field(default=FragmentType.FULL_IGG)
    fluorophore: str = Field(default="", description="Conjugated fluorophore name")
    host_species: HostSpecies = Field(default=HostSpecies.DONKEY, description="Species that raised the secondary")
    pre_adsorbed: list[str] = Field(default_factory=list, description="Cross-absorbed against species")
    catalog_no: str = Field(default="")
    vendor: str = Field(default="")
    lot_no: str = Field(default="")
    dilution: str = Field(default="1:500")
    concentration: float = Field(default=0)
    notes: str = Field(default="")

    @property
    def display_name(self) -> str:
        return (
            f"{self.host_species.value} Anti-{self.target_host.value} "
            f"{self.isotype_specificity} {self.fragment_type.value} "
            f"[{self.fluorophore}]"
        )


# ─── Inventory Model ────────────────────────────────────────────────────────

class InventoryItem(BaseModel):
    """An antibody in the lab's physical inventory."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    antibody_name: str = Field(default="")
    target: str = Field(default="")
    catalog_no: str = Field(default="")
    lot_no: str = Field(default="")
    vendor: str = Field(default="")
    host_species: HostSpecies = Field(default=HostSpecies.RABBIT)
    isotype: str = Field(default="IgG")
    clonality: Clonality = Field(default=Clonality.MONOCLONAL)
    clone_name: str = Field(default="")
    conjugate: str = Field(default="Unconjugated")
    validated_applications: list[str] = Field(default_factory=list)
    reactivity: list[str] = Field(default_factory=list)
    concentration: float = Field(default=0, description="mg/mL")
    total_volume_ul: float = Field(default=0, description="Total volume remaining (µL)")
    aliquot_size_ul: float = Field(default=0, description="Volume per aliquot (µL)")
    storage_temp: StorageTemp = Field(default=StorageTemp.MINUS_20)
    expiry_date: Optional[date] = None
    date_received: Optional[date] = None
    date_opened: Optional[date] = None
    location: str = Field(default="", description="Freezer/box/rack/position")
    working_dilution_if: str = Field(default="")
    working_dilution_fc: str = Field(default="")
    working_dilution_ihc: str = Field(default="")
    freeze_thaw_count: int = Field(default=0)
    notes: str = Field(default="")
    datasheet_url: str = Field(default="")
    purchase_price: float = Field(default=0)
    quantity_per_unit: str = Field(default="")

    def to_primary_antibody(self) -> PrimaryAntibody:
        """Convert inventory item to a PrimaryAntibody for panel building."""
        return PrimaryAntibody(
            target=self.target,
            host_species=self.host_species,
            isotype=self.isotype,
            clonality=self.clonality,
            clone_name=self.clone_name,
            conjugate=self.conjugate,
            validated_applications=self.validated_applications,
            reactivity=self.reactivity,
            concentration=self.concentration,
            catalog_no=self.catalog_no,
            vendor=self.vendor,
            lot_no=self.lot_no,
        )

    @property
    def is_low_stock(self) -> bool:
        """Check if volume is below one aliquot."""
        if self.aliquot_size_ul > 0:
            return self.total_volume_ul < self.aliquot_size_ul
        return self.total_volume_ul < 10  # default threshold

    @property
    def is_expiring_soon(self) -> bool:
        """Check if expiry is within 90 days."""
        if not self.expiry_date:
            return False
        days_left = (self.expiry_date - date.today()).days
        return 0 < days_left <= 90

    @property
    def is_expired(self) -> bool:
        if not self.expiry_date:
            return False
        return self.expiry_date < date.today()


# ─── Panel Design Models ────────────────────────────────────────────────────

class PanelTarget(BaseModel):
    """A single target within a panel design."""
    target_name: str
    expression_level: ExpressionLevel = Field(default=ExpressionLevel.MEDIUM)
    subcellular_location: SubcellularLocation = Field(default=SubcellularLocation.CYTOPLASMIC)
    primary: Optional[PrimaryAntibody] = None
    secondary: Optional[SecondaryAntibody] = None
    fluorophore: Optional[str] = None
    channel: Optional[str] = None
    is_in_house: bool = Field(default=False)
    inventory_item_id: Optional[str] = None
    is_coip_target: bool = Field(default=False)


class ControlEntry(BaseModel):
    """A control in the experiment."""
    control_type: str
    purpose: str
    preparation: str = Field(default="")
    is_required: bool = Field(default=True)


class Panel(BaseModel):
    """A complete antibody panel design."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str = Field(default="Untitled Panel")
    experiment_type: ExperimentType = Field(default=ExperimentType.ICC)
    sample_type: SampleType = Field(default=SampleType.CULTURED_CELLS)
    sample_species: SampleSpecies = Field(default=SampleSpecies.HUMAN)
    instrument_profile: Optional[InstrumentProfile] = None
    targets: list[PanelTarget] = Field(default_factory=list)
    controls: list[ControlEntry] = Field(default_factory=list)
    num_samples: int = Field(default=1)
    researcher_name: str = Field(default="")
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    notes: str = Field(default="")


# ─── Validation Models ──────────────────────────────────────────────────────

class ValidationResult(BaseModel):
    """Result from the rules engine for a single rule check."""
    rule_id: str = Field(description="e.g. R01, R02")
    severity: Severity
    message: str
    affected_targets: list[str] = Field(default_factory=list)
    suggestion: str = Field(default="")


# ─── Pricing Models ─────────────────────────────────────────────────────────

class PriceResult(BaseModel):
    """A scraped or cached antibody price result."""
    catalog_no: str = Field(default="")
    vendor: str = Field(default="")
    product_name: str = Field(default="")
    target: str = Field(default="")
    host_species: str = Field(default="")
    isotype: str = Field(default="")
    clonality: str = Field(default="")
    conjugate: str = Field(default="")
    validated_applications: list[str] = Field(default_factory=list)
    reactivity: list[str] = Field(default_factory=list)
    price: float = Field(default=0)
    package_size: str = Field(default="")
    url: str = Field(default="")
    review_score: float = Field(default=0)
    num_reviews: int = Field(default=0)
    scraped_at: Optional[datetime] = None
    is_cached: bool = Field(default=False)


# ─── Alternative Plan Models ────────────────────────────────────────────────

class AlternativePlan(BaseModel):
    """An alternative panel design for comparison."""
    plan_id: str = Field(default_factory=lambda: f"Plan-{str(uuid.uuid4())[:8]}")
    plan_label: str = Field(default="Plan A")
    panel: Panel
    total_cost: float = Field(default=0)
    validation_score: float = Field(default=0, description="0-100, higher is better")
    overlap_score: float = Field(default=0, description="Lower is better")
    needs_procurement: list[str] = Field(default_factory=list)
    num_errors: int = Field(default=0)
    num_warnings: int = Field(default=0)


# ─── Protocol Models ────────────────────────────────────────────────────────

class ProtocolSection(BaseModel):
    """A single section of the generated protocol."""
    title: str
    content: str
    order: int = Field(default=0)
    is_editable: bool = Field(default=True)


class Protocol(BaseModel):
    """A complete experimental protocol."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    panel_id: str = Field(default="")
    title: str = Field(default="Untitled Protocol")
    author: str = Field(default="")
    lab_name: str = Field(default="")
    institution: str = Field(default="")
    created_date: date = Field(default_factory=date.today)
    version: str = Field(default="1.0")
    sections: list[ProtocolSection] = Field(default_factory=list)
    controls: list[ControlEntry] = Field(default_factory=list)
    panel: Optional[Panel] = None
    notes: str = Field(default="")


# ─── Cost Estimation Models ─────────────────────────────────────────────────

class CostLineItem(BaseModel):
    """A single line item in the cost estimate."""
    item_name: str
    catalog_no: str = Field(default="")
    vendor: str = Field(default="")
    unit_price: float = Field(default=0)
    units_needed: int = Field(default=1)
    total_price: float = Field(default=0)
    is_in_house: bool = Field(default=False)
    notes: str = Field(default="")


class CostEstimate(BaseModel):
    """Complete cost breakdown for a panel."""
    in_house_items: list[CostLineItem] = Field(default_factory=list)
    to_purchase: list[CostLineItem] = Field(default_factory=list)
    subtotal_antibodies: float = Field(default=0)
    subtotal_consumables: float = Field(default=0)
    imaging_cost: float = Field(default=0)
    grand_total: float = Field(default=0)


# ─── Usage Logging ──────────────────────────────────────────────────────────

class UsageLogEntry(BaseModel):
    """Records antibody usage per experiment."""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    inventory_item_id: str
    experiment_name: str = Field(default="")
    panel_id: str = Field(default="")
    volume_used_ul: float = Field(default=0)
    date_used: date = Field(default_factory=date.today)
    notes: str = Field(default="")
