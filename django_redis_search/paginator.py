from typing import Union

from django.core.paginator import EmptyPage, Page, PageNotAnInteger, Paginator


class RediSearchPaginator(Paginator):
    """Custom Paginator that Allows Pagination of a Redis Search Query"""

    def validate_number(self, number: Union[int, str]) -> int:
        try:
            number = int(number)
        except (TypeError, ValueError):
            raise PageNotAnInteger("That page number is not an integer")
        if number < 1:
            raise EmptyPage("That page number is less than 1")
        return number

    def page(self, number: Union[int, str]) -> Page:
        number = self.validate_number(number)
        bottom = (number - 1) * self.per_page
        # Set limit and offset for the redis search query
        self.object_list.paginate(offset=bottom, limit=self.per_page)
        # Executes the redis search query which returns RediSearchResult object
        result = self.object_list.execute(exhaust_results=False)

        page = Page(result, number, self)

        if number > self.num_pages:
            if number == 1 and self.allow_empty_first_page:
                pass
            else:
                raise EmptyPage("That page contains no results")

        return page
