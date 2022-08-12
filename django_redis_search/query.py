from typing import Any, Generator, List, Type, Union

from django.db import models
from django.db.models import Case, IntegerField, When
from redis_om import FindQuery, RedisModel


class RediSearchResult:
    """Class That Stores Redis Search Results"""

    def __init__(
        self,
        results: List[RedisModel],
        hit_count: int,
        django_model: Union[models.Model, None],
    ):
        self.results = results
        self.hit_count = hit_count
        self.django_model = django_model

    def __str__(self) -> str:
        return repr(self)

    def __repr__(self) -> str:
        return "<{} {} of {}>".format(
            self.__class__.__name__, len(self), self.hit_count
        )

    def __iter__(self, *args: Any, **kwargs: Any) -> Generator[Any, None, None]:
        yield from self.results

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, index: int) -> RedisModel:
        return self.results[index]

    def __setitem__(self, index: int, value: RedisModel) -> None:
        self.results[index] = value

    def __bool__(self) -> bool:
        return bool(self.results)

    def __class_getitem__(cls, *args: Any, **kwargs: Any) -> Type["RediSearchResult"]:
        return cls

    def clear(self) -> None:
        """Clears the results"""
        self.results = []
        self.hit_count = 0

    def add(self, data: Union[RedisModel, List[RedisModel]]) -> None:
        """Adds data to the results"""
        if isinstance(data, list):
            self.results += data
        else:
            self.results.append(data)

    def count(self) -> int:
        """
        Returns the number of hits.

        This is not same as `len(self.results)` as
        `self.results` may contain only paginated results.
        """
        return self.hit_count

    def exists(self) -> bool:
        """Returns True if there are results"""
        return bool(self.results)

    def to_queryset(self) -> models.QuerySet:
        """Converts the search results to a Django QuerySet"""
        if not self.django_model:
            raise ValueError("No Django Model has been set")

        # if no results, return empty queryset
        if not self.results:
            return self.django_model.objects.none()

        pks = [result.pk for result in self.results]
        return self.django_model.objects.filter(pk__in=pks).order_by(
            Case(
                *[When(pk=pk, then=position) for position, pk in enumerate(pks)],
                output_field=IntegerField(),
            )
        )


class RediSearchQuery(FindQuery):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.django_model = kwargs.pop("django_model", None)
        super().__init__(*args, **kwargs)
        # Initialize the cache with empty RediSearchResult.
        self._model_cache: RediSearchResult = RediSearchResult(
            results=[], hit_count=0, django_model=self.django_model
        )

    def to_queryset(self) -> models.QuerySet:
        """Converts the search results to a Django QuerySet"""
        if self._model_cache:
            return self._model_cache.to_queryset()
        # If no results, then execute the query and return Django QuerySet.
        return self.execute().to_queryset()

    def all(self, batch_size: int = 10) -> "RediSearchQuery":
        """
        Only Update batch size and return instance.

        Overridden so that we do not run `execute()` if this method is called.
        """
        if batch_size != self.page_size:
            query = self.copy(page_size=batch_size, limit=batch_size)
            return query
        return self

    def count(self) -> int:
        """Return the number of hits from `_model_cache`"""
        return self._model_cache.count()

    def exists(self) -> bool:
        """Returns True if there are results from `_model_cache`"""
        return self._model_cache.exists()

    def copy(self, **kwargs: Any) -> "RediSearchQuery":
        """ "Returns a copy of the class"""
        original = self.dict()
        original.update(**kwargs)
        return self.__class__(**original)

    def paginate(self, offset: int = 0, limit: int = 100) -> None:
        """Paginates the results."""
        self.offset = offset
        self.limit = limit

    def execute(self, exhaust_results: bool = True) -> RediSearchResult:
        """Executes the search query and returns a RediSearchResult"""
        args = ["ft.search", self.model.Meta.index_name, self.query, *self.pagination]
        if self.sort_fields:
            args += self.resolve_redisearch_sort_fields()

        # Reset the cache if we're executing from offset 0.
        if self.offset == 0:
            self._model_cache.clear()

        # If the offset is greater than 0, we're paginating through a result set,
        # so append the new results to results already in the cache.
        raw_result = self.model.db().execute_command(*args)
        count = raw_result[0]
        results = self.model.from_redis(raw_result)
        # Update the cache with the new results.
        self._model_cache.add(results)
        self._model_cache.hit_count = count

        if not exhaust_results:
            return self._model_cache

        # The query returned all results, so we have no more work to do.
        if count <= len(results):
            return self._model_cache

        # Transparently (to the user) make subsequent requests to paginate
        # through the results and finally return them all.
        query = self
        while True:
            # Make a query for each pass of the loop, with a new offset equal to the
            # current offset plus `page_size`, until we stop getting results back.
            query = query.copy(offset=query.offset + query.page_size)
            _results = query.execute(exhaust_results=False)
            if not _results:
                break
            self._model_cache.add(_results)

        return self._model_cache
