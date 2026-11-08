"""The plan page layout a player uses (preference `planLayout`). Structure and limits only: the frontend owns
what the element ids mean, their minimum sizes and new elements."""

from typing import Annotated, Literal, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints, model_validator

MAX_ITEMS = 40
MAX_ELEMENTS = 40
MAX_KNOWN = 64
H_MIN = 64
H_MAX = 4000

ElementId = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_-]{0,31}$')]
ItemId = Annotated[str, StringConstraints(pattern=r'^[a-z0-9_-]{1,40}$')]


def _unique(ids: list[str]) -> list[str]:
    if len(set(ids)) != len(ids):
        raise ValueError('each id at most once')
    return ids


ElementIds = Annotated[list[ElementId], Field(max_length=MAX_ELEMENTS), AfterValidator(_unique)]


class _Strict(BaseModel):
    # strict: the stored value is exactly what was sent, no "3" -> 3
    model_config = ConfigDict(extra='forbid', strict=True)


class PlanLayoutItem_V1(_Strict):
    """A box on the grid: one element, or several as tabs."""

    id: ItemId
    els: list[ElementId] = Field(min_length=1, max_length=MAX_ELEMENTS)
    active: ElementId
    x: int = Field(ge=0)
    w: int = Field(ge=1)
    y: int = Field(ge=0, le=999)
    hm: Literal['fit', 'cap', 'fixed']
    h: int = Field(ge=0, le=H_MAX)
    pin: bool
    follow: Literal[True] | None = None
    # an element shown in the tab of the element before it in els
    join: dict[ElementId, Literal['below', 'beside']] | None = None
    # beside: columns of the part before it; below: its height in px
    joinSize: dict[ElementId, int] | None = None  # the wire name

    @model_validator(mode='after')
    def _check(self) -> Self:
        join = self.join or {}
        if self.active not in self.els:
            raise ValueError('active must be one of els')
        if self.active in join:
            raise ValueError('active must not be a joined element')
        # with fit, h may keep the height of an earlier cap/fixed, so switching back restores it
        if (self.hm != 'fit' and self.h == 0) or 0 < self.h < H_MIN:
            raise ValueError('h must be 64-4000, or 0 with hm fit')
        if self.follow and self.pin:
            raise ValueError('follow is not allowed with pin')
        for el in join:
            if el not in self.els or el == self.els[0]:
                raise ValueError(f'join.{el}: must be one of els, not the first')
            if self.els[self.els.index(el) - 1] in join:
                raise ValueError(f'join.{el}: a tab holds two elements at most')
        for el, size in (self.joinSize or {}).items():
            if el not in join:
                raise ValueError(f'joinSize.{el}: needs a join')
            low, high = (1, self.w - 1) if join[el] == 'beside' else (H_MIN, H_MAX)
            if not low <= size <= high:
                raise ValueError(f'joinSize.{el}: must be {low}-{high}')
        return self


class PlanLayoutGrid_V1(_Strict):
    """One arrangement: a grid with its items, Everything open and table view."""

    cols: int
    gap: Literal[8, 12, 16, 24]
    items: list[PlanLayoutItem_V1] = Field(min_length=1, max_length=MAX_ITEMS)
    open: ElementIds | None = None
    table: ElementIds | None = None

    @model_validator(mode='after')
    def _check(self) -> Self:
        ids: set[str] = set()
        els: list[str] = []
        for i, item in enumerate(self.items):
            if item.x + item.w > self.cols:
                raise ValueError(f'items.{i}: x + w must be at most cols')
            if item.id in ids:
                raise ValueError(f'items.{i}.id: each item id at most once')
            ids.add(item.id)
            els += item.els
        if len(set(els)) != len(els):
            raise ValueError('each element at most once across items')
        if len(els) > MAX_ELEMENTS:
            raise ValueError(f'at most {MAX_ELEMENTS} elements')
        return self


class PlanLayoutWide_V1(PlanLayoutGrid_V1):
    cols: int = Field(ge=4, le=24)


class PlanLayoutPhone_V1(PlanLayoutGrid_V1):
    cols: Literal[2]


class PlanLayoutCustom_V1(_Strict):
    """The one Custom Layout; without phone a phone stacks wide."""

    wide: PlanLayoutWide_V1
    phone: PlanLayoutPhone_V1 | None = None
    # the elements the layout has seen; missing: the catalogue at launch
    known: Annotated[list[ElementId], Field(max_length=MAX_KNOWN), AfterValidator(_unique)] | None = None


class PlanLayoutFolded_V1(_Strict):
    """Folded items by their first element, per arrangement."""

    wide: ElementIds = Field(default_factory=list)
    phone: ElementIds = Field(default_factory=list)


class PlanLayout_V1(_Strict):
    v: Literal[1]
    preset: Literal['standard', 'custom']
    custom: PlanLayoutCustom_V1 | None = None
    folded: PlanLayoutFolded_V1 = Field(default_factory=PlanLayoutFolded_V1)
    tinted: bool | None = None

    @model_validator(mode='after')
    def _check(self) -> Self:
        if self.preset == 'custom' and self.custom is None:
            raise ValueError('preset custom needs custom')
        return self
