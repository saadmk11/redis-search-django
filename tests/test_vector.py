from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from django.core.exceptions import FieldError

from redis_search_django.documents import Document
from redis_search_django.embeddings import (
    as_floats,
    call_embed,
    call_embed_query,
    coerce_embedder,
    is_vector,
    resolve_embedder,
    resolve_source,
)
from redis_search_django.exceptions import ConfigurationError
from redis_search_django.fields import Vector
from redis_search_django.query.knn import wrap_knn_query
from redis_search_django.schema import build_schema
from redis_search_django.serializer import Serializer

from .conftest import make_document
from .helpers import NOT_AN_EMBEDDER, color_embed, is_redis_running
from .models import Category, Product, Vendor


class ColorEmbedder:
    def embed(self, value: str) -> list[float]:
        return color_embed(value)

    def embed_query(self, value: str) -> list[float]:
        return color_embed(value)


class QueryOnlyEmbedder:
    def embed(self, value: str) -> list[float]:
        return [1.0, 0.0, 0.0, 0.0]

    def embed_query(self, value: str) -> list[float]:
        return [0.0, 1.0, 0.0, 0.0]


class _NotCallable:
    embed = "nope"


def _vector_document(
    name: str,
    *,
    embedder=color_embed,
    source="name",
    extra=None,
    storage="json",
    **vector_kwargs,
):
    attrs = {
        "embedding": Vector(
            dims=4,
            algorithm="FLAT",
            source=source,
            embedder=embedder,
            **vector_kwargs,
        )
    }
    if extra:
        attrs.update(extra)
    index = {
        "name": f"idx:test.category.{name.lower()}.{uuid.uuid4().hex[:8]}",
        "prefix": f"rsd:test.category.{name.lower()}.{uuid.uuid4().hex[:8]}:",
        "storage": storage,
    }
    return make_document(
        name,
        Category,
        ["name"],
        extra_attrs={
            "embedding": attrs["embedding"],
            "Index": type("Index", (), index),
        },
    )


class _ArrayLike:
    def __init__(self, data):
        self._data = data

    def tolist(self):
        return list(self._data)


class _Float32:
    def __init__(self, number):
        self.number = number

    def __float__(self):
        return float(self.number)


def test_is_vector_rejects_text_bytes_and_bools():
    assert is_vector([1.0, 2.0]) is True
    assert is_vector((0, 1)) is True
    assert is_vector("1.0") is False
    assert is_vector(b"\x00") is False
    assert is_vector([]) is False
    assert is_vector(None) is False
    assert is_vector([True, False]) is False
    assert is_vector(memoryview(b"ab")) is False
    assert is_vector(_ArrayLike([1.0, 2.0])) is True
    assert is_vector([_Float32(1), _Float32(2)]) is True
    assert is_vector([object()]) is False

    class _ToInt:
        def tolist(self):
            return 12

    assert is_vector(_ToInt()) is False

    class _BadList:
        def tolist(self):
            raise RuntimeError("nope")

    assert is_vector(_BadList()) is False

    class _ToText:
        def tolist(self):
            return "12"

    assert is_vector(_ToText()) is False


def test_as_floats_validates_dims_and_type():
    assert as_floats([1, 2.0], field_name="emb", dims=2) == [1.0, 2.0]
    assert as_floats(_ArrayLike([1, 2]), field_name="emb", dims=2) == [1.0, 2.0]
    assert as_floats([_Float32(1), _Float32(2)], field_name="emb", dims=2) == [
        1.0,
        2.0,
    ]
    with pytest.raises(ConfigurationError, match="numeric sequence"):
        as_floats("red", field_name="emb", dims=2)
    with pytest.raises(ConfigurationError, match="2 dimensions"):
        as_floats([1.0], field_name="emb", dims=2)


def test_coerce_embedder_accepts_callable_protocol_and_path():
    assert coerce_embedder(color_embed) is color_embed
    instance = ColorEmbedder()
    assert coerce_embedder(instance) is instance
    resolved = coerce_embedder("tests.helpers.color_embed")
    assert resolved is color_embed
    with pytest.raises(ConfigurationError, match="not callable"):
        coerce_embedder("tests.helpers.NOT_AN_EMBEDDER")
    with pytest.raises(ConfigurationError, match="not callable"):
        coerce_embedder(NOT_AN_EMBEDDER)


def test_resolve_embedder_order_and_hooks():
    field = Vector(dims=4, embedder=color_embed)
    field.bind("embedding", object)
    assert resolve_embedder(object, field) is color_embed

    class Hooked(Document):
        embedding = Vector(dims=4, algorithm="FLAT", source="name")

        class Django:
            model = Category

        class Index:
            name = "idx:test.category.embhook"
            prefix = "rsd:test.category.embhook:"

        @classmethod
        def embed_embedding(cls, value: str) -> list[float]:
            return color_embed(value)

    hook_field = Hooked._meta.fields["embedding"]
    resolved = resolve_embedder(Hooked, hook_field)
    assert resolved is not None
    assert list(resolved("red")) == color_embed("red")

    class Defaulted(Document):
        embedder = color_embed
        embedding = Vector(dims=4, algorithm="FLAT", source="name")

        class Django:
            model = Category

        class Index:
            name = "idx:test.category.embdef"
            prefix = "rsd:test.category.embdef:"

    default_field = Defaulted._meta.fields["embedding"]
    assert resolve_embedder(Defaulted, default_field) is color_embed
    bare = Vector(dims=4)
    bare.bind("embedding", object)
    assert resolve_embedder(object, bare) is None


def test_call_embed_and_embed_query_variants():
    assert call_embed(color_embed, "red") == [1.0, 0.0, 0.0, 0.0]
    assert call_embed(ColorEmbedder(), "blue") == [0.0, 1.0, 0.0, 0.0]
    split = QueryOnlyEmbedder()
    assert call_embed(split, "x") == [1.0, 0.0, 0.0, 0.0]
    assert call_embed_query(split, "x") == [0.0, 1.0, 0.0, 0.0]
    assert call_embed_query(color_embed, "red") == [1.0, 0.0, 0.0, 0.0]
    with pytest.raises(ConfigurationError, match="instance"):
        call_embed(ColorEmbedder, "red")
    with pytest.raises(ConfigurationError, match="not callable"):
        call_embed(_NotCallable(), "red")


def test_resolve_source_walks_and_rejects_typos():
    class Inner:
        name = "red"
        other = object()

    class Box:
        inner = Inner()
        missing = None

    assert resolve_source(Box(), "inner.name") == "red"
    assert resolve_source(Box(), "inner.other") == str(Inner.other)
    assert resolve_source(Box(), "missing.name") is None
    with pytest.raises(ConfigurationError, match="does not exist"):
        resolve_source(Box(), "nope")


@pytest.mark.django_db
def test_vector_prepare_embeds_source_on_save(category_obj):
    doc = _vector_document("PrepSrc", embedder="tests.helpers.color_embed")
    payload = Serializer().to_document(doc, category_obj)
    assert payload["embedding"] == color_embed("Test")

    class Hooked(Document):
        embedding = Vector(dims=4, algorithm="FLAT", source="name")

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.serhook"
            prefix = "rsd:test.category.serhook:"

        @classmethod
        def embed_embedding(cls, value: str) -> list[float]:
            return color_embed(value)

    assert Serializer().to_document(Hooked, category_obj)["embedding"] == color_embed(
        "Test"
    )

    class Defaulted(Document):
        embedder = color_embed
        embedding = Vector(dims=4, algorithm="FLAT", source="name")

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.serdef"
            prefix = "rsd:test.category.serdef:"

    defaulted = Serializer().to_document(Defaulted, category_obj)
    assert defaulted["embedding"] == color_embed("Test")


@pytest.mark.django_db
def test_vector_prepare_hook_can_return_text_or_vector(category_obj):
    class FromText(Document):
        embedding = Vector(dims=4, algorithm="FLAT", embedder=color_embed)

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.preptext"
            prefix = "rsd:test.category.preptext:"

        @classmethod
        def prepare_embedding(cls, instance):
            return instance.name

    class FromVec(Document):
        embedding = Vector(dims=4, algorithm="FLAT")

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.prepvec"
            prefix = "rsd:test.category.prepvec:"

        @classmethod
        def prepare_embedding(cls, instance):
            return [0.1, 0.2, 0.3, 0.4]

    assert Serializer().to_document(FromText, category_obj)["embedding"] == color_embed(
        "Test"
    )
    assert Serializer().to_document(FromVec, category_obj)["embedding"] == [
        0.1,
        0.2,
        0.3,
        0.4,
    ]


@pytest.mark.django_db
def test_vector_prepare_requires_embedder_for_text(category_obj):
    class Bare(Document):
        embedding = Vector(dims=4, algorithm="FLAT", source="name")

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.barevec"
            prefix = "rsd:test.category.barevec:"

    with pytest.raises(ConfigurationError, match="embedder"):
        Serializer().to_document(Bare, category_obj)


@pytest.mark.django_db
def test_vector_source_typo_raises(category_obj):
    class Typo(Document):
        embedding = Vector(
            dims=4, algorithm="FLAT", source="namme", embedder=color_embed
        )

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.typosrc"
            prefix = "rsd:test.category.typosrc:"

    with pytest.raises(ConfigurationError, match="does not exist"):
        Serializer().to_document(Typo, category_obj)

    field = Vector(dims=4, source="name", embedder=color_embed)
    field.bind("embedding", Typo)

    class Empty:
        name = None

    assert field.prepare(Empty(), Typo) is None

    omitted = Vector(dims=4)
    omitted.bind("embedding", Typo)

    class NoAttr:
        pass

    assert omitted.prepare(NoAttr(), Typo) is None

    short = Vector(dims=4, source="name")
    short.bind("embedding", Typo)

    class NumericName:
        name = [1.0, 2.0]

    with pytest.raises(ConfigurationError, match="dimensions"):
        short.prepare(NumericName(), Typo)


def test_vector_to_index_value_json_hash_and_blob():
    field = Vector(dims=2, type="FLOAT32")
    field.bind("emb", object)
    assert field.to_index_value(None) is None
    assert field.to_index_value([1.0, 2.0], storage="json") == [1.0, 2.0]
    blob = field.to_blob([1.0, 2.0])
    assert field.to_index_value([1.0, 2.0], storage="hash") == blob
    assert field.from_blob(blob) == pytest.approx([1.0, 2.0])
    assert field.to_index_value(blob, storage="json") == pytest.approx([1.0, 2.0])
    assert field.to_index_value(bytearray(blob), storage="json") == pytest.approx(
        [1.0, 2.0]
    )
    wide = Vector(dims=2, type="FLOAT64")
    wide.bind("emb", object)
    packed = wide.to_blob([1.0, 2.0])
    assert wide.from_blob(packed) == pytest.approx([1.0, 2.0])
    with pytest.raises(ConfigurationError, match="blob"):
        field.from_blob(b"xx")


def test_schema_fingerprint_ignores_embedder():
    class Left(Document):
        embedding = Vector(dims=4, source="name", embedder=color_embed)

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.fpleft"
            prefix = "rsd:test.category.fpleft:"

    class Right(Document):
        embedding = Vector(dims=4)

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.fpright"
            prefix = "rsd:test.category.fpright:"

    left = {field.alias: field.extra for field in build_schema(Left).fields}
    right = {field.alias: field.extra for field in build_schema(Right).fields}
    assert left["embedding"] == right["embedding"]
    assert "embedder" not in left["embedding"]
    assert "source" not in left["embedding"]


def test_knn_raw_wraps_filters_and_infers_field():
    doc = _vector_document("KnnRaw")
    qs = doc.objects.filter(name="red").knn("crimson", k=5, ef_runtime=20)
    query, params = qs.raw()
    assert query.startswith('(@name:("red"))=>[KNN $rsd_knn_k @embedding')
    assert "EF_RUNTIME 20" in query
    assert "AS vector_score" in query
    assert params["rsd_knn_k"] == "5"
    assert params["rsd_knn_vec"] == Vector(dims=4).to_blob(color_embed("crimson"))
    star, _params = doc.objects.knn([1.0, 0.0, 0.0, 0.0], k=3).raw()
    assert star.startswith("*=>[KNN ")


def test_knn_field_resolution_errors(document_class):
    none = document_class("NoVec", Category, ["name"])
    with pytest.raises(FieldError, match="no Vector field"):
        none.objects.knn("red")

    class Two(Document):
        a = Vector(dims=4, algorithm="FLAT", embedder=color_embed, source="name")
        b = Vector(dims=4, algorithm="FLAT", embedder=color_embed, source="name")

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.twovec"
            prefix = "rsd:test.category.twovec:"

    with pytest.raises(FieldError, match="multiple Vector"):
        Two.objects.knn("red")
    query, _params = Two.objects.knn("red", field="b").raw()
    assert "@b $" in query
    with pytest.raises(FieldError, match="Cannot resolve"):
        Two.objects.knn("red", field="missing")
    with pytest.raises(FieldError, match="not a Vector"):
        Two.objects.knn("red", field="name")


def test_knn_validates_k_ef_and_score_name():
    doc = _vector_document("KnnVal")
    with pytest.raises(ValueError, match="k must"):
        doc.objects.knn("red", k=0)
    with pytest.raises(ValueError, match="k must"):
        doc.objects.knn("red", k=True)
    with pytest.raises(ValueError, match="ef_runtime"):
        doc.objects.knn("red", ef_runtime=0)
    with pytest.raises(ValueError, match="ef_runtime"):
        doc.objects.knn("red", ef_runtime=True)
    with pytest.raises(ValueError, match="score_name"):
        doc.objects.knn("red", score_name="vector-score")


def test_knn_string_query_requires_embedder():
    class Bare(Document):
        embedding = Vector(dims=4, algorithm="FLAT")

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            name = "idx:test.category.knnnoemb"
            prefix = "rsd:test.category.knnnoemb:"

    with pytest.raises(ConfigurationError, match="embedder"):
        Bare.objects.knn("red")
    query, params = Bare.objects.knn([1.0, 0.0, 0.0, 0.0]).raw()
    assert query.startswith("*=>[KNN ")
    assert isinstance(params["rsd_knn_vec"], bytes)


def test_knn_uses_embed_query_for_text():
    doc = _vector_document("KnnQEmb", embedder=QueryOnlyEmbedder())
    _query, params = doc.objects.knn("anything").raw()
    assert params["rsd_knn_vec"] == Vector(dims=4).to_blob([0.0, 1.0, 0.0, 0.0])


def test_knn_clones_and_sets_sort():
    doc = _vector_document("KnnClone")
    qs = doc.objects.filter(name="red").knn("blue", k=2)
    query, params = qs.raw()
    assert query.startswith('(@name:("red"))=>[KNN ')
    assert params["rsd_knn_k"] == "2"
    assert qs._sort == "vector_score"
    assert qs._sort_desc is False
    ordered = qs.order_by("-name")
    assert ordered._knn is qs._knn
    assert ordered._sort == "name"
    assert ordered._sort_desc is True
    already = doc.objects.order_by("name").knn("blue")
    assert already._sort == "name"
    assert already._knn is not None


def test_knn_and_extra_cannot_be_combined():
    doc = _vector_document("KnnExtra")
    with pytest.raises(ValueError, match="knn\\(\\) after extra"):
        doc.objects.extra(query="*").knn("red")
    with pytest.raises(ValueError, match="extra\\(\\) after knn"):
        doc.objects.knn("red").extra(query="*")


def test_wrap_knn_star_and_filter():
    from redis_search_django.query.knn import KnnClause

    field = Vector(dims=2)
    field.bind("emb", object)
    knn = KnnClause(
        alias="emb",
        field=field,
        blob=b"\x00\x00",
        k=3,
        ef_runtime=None,
        score_name="vector_score",
    )
    query, params = wrap_knn_query("*", knn, None)
    assert query.startswith("*=>[KNN ")
    assert params["rsd_knn_k"] == "3"


def test_iter_hash_vectors_includes_object_children():
    from redis_search_django import fields
    from redis_search_django.query.queryset import _has_vector, _iter_hash_vectors

    vendor_doc = make_document(
        "VendVec",
        Vendor,
        ["name"],
        embedded=True,
        extra_attrs={"emb": Vector(dims=2)},
    )

    class WithNested(Document):
        vendor = fields.Object(vendor_doc)
        embedding = Vector(dims=2)

        class Django:
            model = Product
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.product.objvec"
            prefix = "rsd:test.product.objvec:"

    names = {name for name, _field in _iter_hash_vectors(WithNested)}
    assert "embedding" in names
    assert "vendor__emb" in names
    assert _has_vector(WithNested) is True


def test_knn_count_omits_document_bodies():
    doc = _vector_document("KnnCount")
    qs = doc.objects.knn("red", k=4)
    query, _params = qs._search_args(offset=0, limit=4, content=False)
    assert query._no_content is True
    query_full, _params = qs._search_args(offset=0, limit=4, content=True)
    assert query_full._no_content is False


def test_knn_none_is_empty():
    doc = _vector_document("KnnNone")
    qs = doc.objects.knn("red").none()
    assert qs.count() == 0
    assert list(qs) == []


@pytest.mark.django_db
async def test_load_hash_with_and_without_vectors(document_class):
    from redis_search_django.query.queryset import _aload_hash, _load_hash

    plain = document_class("HashNoV", Category, ["name"])
    client = SimpleNamespace(hgetall=lambda key: {"name": "x"})
    assert _load_hash(client, "k", plain) == {"name": "x"}

    class HashVec(Document):
        embedding = Vector(dims=2)

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.category.loadvec"
            prefix = "rsd:test.category.loadvec:"

    blob = Vector(dims=2).to_blob([1.0, 2.0])

    class Fake:
        def execute_command(self, *args, **kwargs):
            return {b"name": b"red", b"embedding": blob}

    out = _load_hash(Fake(), "k", HashVec)
    assert out["name"] == "red"
    assert out["embedding"] == pytest.approx([1.0, 2.0])

    class AsyncFake:
        async def hgetall(self, key):
            return {"name": "x"}

        async def execute_command(self, *args, **kwargs):
            return [b"name", b"red", b"embedding", blob]

    assert await _aload_hash(AsyncFake(), "k", plain) == {"name": "x"}
    loaded = await _aload_hash(AsyncFake(), "k", HashVec)
    assert loaded["embedding"] == pytest.approx([1.0, 2.0])


def test_hash_helpers_decode_mixed_values():
    from redis_search_django.query.queryset import (
        _coerce_hash_vectors,
        _maybe_decode,
        _maybe_decode_value,
        _pairs_to_hash,
    )

    assert _maybe_decode(b"pk") == "pk"
    assert _maybe_decode("pk") == "pk"
    assert _maybe_decode_value("red") == "red"
    assert _maybe_decode_value(b"red") == "red"
    blob = Vector(dims=2).to_blob([1.0, 2.0])
    assert _maybe_decode_value(blob) == blob
    as_dict = _pairs_to_hash({b"name": b"red"})
    assert as_dict == {"name": b"red"}
    as_list = _pairs_to_hash([b"name", b"red", b"emb", blob])
    assert as_list["name"] == b"red"
    assert as_list["emb"] == blob

    class HashDoc(Document):
        embedding = Vector(dims=2)

        class Django:
            model = Category
            fields = ["name"]

        class Index:
            storage = "hash"
            name = "idx:test.category.hashhelp"
            prefix = "rsd:test.category.hashhelp:"

    data = {"name": "red", "embedding": blob}
    out = _coerce_hash_vectors(HashDoc, data)
    assert out["embedding"] == pytest.approx([1.0, 2.0])
    already = {"embedding": [1.0, 2.0], "name": "red"}
    assert _coerce_hash_vectors(HashDoc, already)["embedding"] == [1.0, 2.0]
    utf8_blob = Vector(dims=2).to_blob([2.0, 2.0])
    decoded = utf8_blob.decode("utf-8")
    coerced = _coerce_hash_vectors(HashDoc, {"embedding": decoded, "name": "x"})
    assert coerced["embedding"] == pytest.approx([2.0, 2.0])
    dropped = _coerce_hash_vectors(HashDoc, {"embedding": "not-a-blob", "name": "x"})
    assert "embedding" not in dropped
    short = _coerce_hash_vectors(HashDoc, {"embedding": b"xx", "name": "x"})
    assert "embedding" not in short


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
def test_knn_ranks_nearest_and_combines_filters():
    from redis_search_django.index import IndexManager
    from redis_search_django.indexer import Indexer

    doc = _vector_document("KnnLive")
    manager = IndexManager(doc)
    manager.create()
    try:
        red = Category.objects.create(name="red")
        crimson = Category.objects.create(name="crimson")
        blue = Category.objects.create(name="blue")
        indexer = Indexer()
        for obj in (red, crimson, blue):
            indexer.upsert(doc, obj)

        hits = list(doc.objects.knn("red", k=3))
        assert [hit.pk for hit in hits[:2]] == [str(red.pk), str(crimson.pk)]
        assert hits[0].score is not None
        assert hits[0].score < hits[1].score
        assert hits[0].vector_score is not None

        filtered = list(
            doc.objects.filter(name__in=["crimson", "blue"]).knn("red", k=2)
        )
        assert [hit.pk for hit in filtered] == [str(crimson.pk), str(blue.pk)]
        assert doc.objects.knn("red", k=3).count() == 3
        assert doc.objects.knn("red", k=3).exists() is True
        sliced = list(doc.objects.knn("red", k=3)[:2])
        assert len(sliced) == 2
        first = doc.objects.knn("red", k=3).first()
        assert first is not None
        assert first.pk == str(red.pk)
        named = list(
            doc.objects.knn("red", k=1, score_name="dist").return_fields("name")
        )
        assert named[0].name == "red"
        assert named[0].score is not None
    finally:
        manager.drop(delete_docs=True)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
def test_knn_hash_storage_and_get_by_pk():
    from redis_search_django.index import IndexManager
    from redis_search_django.indexer import Indexer

    doc = _vector_document("KnnHash", storage="hash")
    manager = IndexManager(doc)
    manager.create()
    try:
        red = Category.objects.create(name="red")
        blue = Category.objects.create(name="blue")
        Indexer().upsert(doc, red)
        Indexer().upsert(doc, blue)
        hits = list(doc.objects.knn("red", k=2))
        assert hits[0].pk == str(red.pk)
        loaded = doc.objects.get(pk=red.pk)
        assert loaded.embedding == pytest.approx(color_embed("red"))
    finally:
        manager.drop(delete_docs=True)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
def test_hash_get_by_pk_and_knn_unpack_utf8_blobs():
    from redis_search_django.index import IndexManager
    from redis_search_django.indexer import Indexer

    twos = [2.0, 2.0, 2.0, 2.0]
    doc = _vector_document("HashTwos", storage="hash", embedder=lambda _v: twos)
    manager = IndexManager(doc)
    manager.create()
    try:
        red = Category.objects.create(name="red")
        Indexer().upsert(doc, red)
        loaded = doc.objects.get(pk=red.pk)
        assert loaded.embedding == pytest.approx(twos)
        hits = list(doc.objects.knn("x", k=1))
        assert hits[0].pk == str(red.pk)
        assert hits[0].embedding == pytest.approx(twos)
    finally:
        manager.drop(delete_docs=True)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
async def test_knn_async_count_and_iteration():
    from asgiref.sync import sync_to_async

    from redis_search_django.index import IndexManager
    from redis_search_django.indexer import Indexer

    doc = _vector_document("KnnAsync")
    manager = IndexManager(doc)
    await sync_to_async(manager.create)()
    try:
        red = await sync_to_async(Category.objects.create)(name="red")
        await sync_to_async(Indexer().upsert)(doc, red)
        assert await doc.objects.knn("red", k=1).acount() == 1
        hits = [hit async for hit in doc.objects.knn("red", k=1)]
        assert hits[0].pk == str(red.pk)
        loaded = await doc.objects.aget(pk=red.pk)
        assert loaded.embedding == pytest.approx(color_embed("red"))
    finally:
        await sync_to_async(manager.drop)(delete_docs=True)


@pytest.mark.skipif(not is_redis_running(), reason="Redis is not running")
@pytest.mark.django_db(transaction=True)
async def test_hash_get_by_pk_async_decodes_vector():
    from asgiref.sync import sync_to_async

    from redis_search_django.index import IndexManager
    from redis_search_django.indexer import Indexer

    doc = _vector_document("KnnAHash", storage="hash")
    manager = IndexManager(doc)
    await sync_to_async(manager.create)()
    try:
        red = await sync_to_async(Category.objects.create)(name="red")
        await sync_to_async(Indexer().upsert)(doc, red)
        loaded = await doc.objects.aget(pk=red.pk)
        assert loaded.embedding == pytest.approx(color_embed("red"))
    finally:
        await sync_to_async(manager.drop)(delete_docs=True)


def test_knn_score_fallback_when_distance_is_garbage():
    from redis_search_django.query.results import SearchResult

    doc = _vector_document("KnnScore")
    qs = doc.objects.knn("red", k=1)
    raw = SimpleNamespace(
        total=1,
        docs=[
            SimpleNamespace(
                id="x:1",
                score=0.5,
                vector_score="not-a-float",
                payload=None,
            )
        ],
    )
    result = qs._result_from_raw(raw)
    assert isinstance(result, SearchResult)
    assert result.hits[0].score == 0.5
    missing = SimpleNamespace(
        total=1,
        docs=[SimpleNamespace(id="x:2", score=0.25, payload=None)],
    )
    assert qs._result_from_raw(missing).hits[0].score == 0.25
    parsed = SimpleNamespace(
        total=1,
        docs=[SimpleNamespace(id="x:3", score=9, vector_score="0.125", payload=None)],
    )
    assert qs._result_from_raw(parsed).hits[0].score == 0.125
    assert qs._window() == (0, 1)
    assert qs[1:4]._window() == (1, 3)
