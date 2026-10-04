from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid')
class Mapping(Strict):
    document_id: str
    sheet: str
    header_row: int = Field(ge=0)
    name_columns: list[str] = Field(min_length=1,max_length=10)
    category_column: str|None = None
class Feature(Strict):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,49}$')
    label: str
    kind: Literal['string','number','integer']
    unit: Literal['none','V','kV','mm','mm2','MPa','bar','m','kg','pcs'] = 'none'
    comparison: Literal['eq','gte','lte','informational'] = 'informational'
    tolerance: float = Field(default=0,ge=0)
    aliases: list[str] = Field(default_factory=list)
class Category(Strict):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,49}$')
    name: str
    parent_id: str|None = None
    feature_keys: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    item_count: int = 0
    requirement_count: int = 0
class MappingDraft(Strict):
    mappings: list[Mapping] = Field(min_length=1)
class ColumnMapping(Strict):
    header_row: int = Field(ge=0)
    name_columns: list[str] = Field(min_length=1,max_length=10)
    category_column: str|None = None
class SchemaDraft(Strict):
    mappings: list[Mapping]
    features: list[Feature] = Field(default_factory=list,max_length=50000)
    categories: list[Category] = Field(default_factory=list,max_length=50000)
    taxonomy_mode: Literal['sample','passports'] = 'sample'
    mapping_approved: bool = False
    taxonomy_status: Literal['pending','review','approved'] = 'pending'
    analyzed_items: int = 0
    @model_validator(mode='after')
    def unique_keys(self):
        if self.taxonomy_mode=='sample' and (not self.features or not self.categories):
            raise ValueError('Legacy schema requires features and categories')
        if self.taxonomy_mode=='passports' and self.taxonomy_status!='pending' and (not self.categories or not self.features):
            raise ValueError('Build taxonomy from normalized passports first')
        for values in ([f.key for f in self.features],[c.id for c in self.categories]):
            if len(set(values))!=len(values): raise ValueError('Duplicate field/category identifiers')
        for f in self.features:
            if f.kind=='string' and (f.unit!='none' or f.comparison in ('gte','lte') or f.tolerance):
                raise ValueError('Text fields only support eq/informational without units or tolerance')
        cats={c.id:c.parent_id for c in self.categories}
        fields={f.key for f in self.features}
        for c in self.categories:
            if not set(c.feature_keys)<=fields: raise ValueError('Unknown category feature')
        for ident in cats:
            seen=set();cur=ident
            while cur is not None:
                if cur in seen or cur not in cats: raise ValueError('Invalid category hierarchy')
                seen.add(cur);cur=cats[cur]
        return self
class ExtractedValue(Strict):
    key: str
    value: str|None
    unit: str|None
    evidence: str|None
class Extraction(Strict):
    category_id: str|None
    category_evidence: str|None
    values: list[ExtractedValue]
class Requirements(Strict):
    items: list[str] = Field(min_length=1,max_length=50)

class OpenValue(Strict):
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,49}$')
    label: str = Field(min_length=1,max_length=150)
    kind: Literal['string','number','integer']
    value: str|None
    unit: str|None
    target_unit: Literal['none','V','kV','mm','mm2','MPa','bar','m','kg','pcs']
    evidence: str|None

class OpenExtraction(Strict):
    name: str = Field(min_length=1,max_length=500)
    name_evidence: str = Field(min_length=1)
    values: list[OpenValue] = Field(max_length=64)

class MaterialLabel(Strict):
    item_id: str
    material: str = Field(min_length=1,max_length=150)
    subtype: str|None = Field(default=None,max_length=150)

class MaterialLabels(Strict):
    items: list[MaterialLabel] = Field(min_length=1,max_length=20)

class Equivalent(Strict):
    match_id: str|None

