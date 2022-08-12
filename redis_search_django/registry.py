from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, Set, Type

from django.db import models

if TYPE_CHECKING:
    from .documents import Document


@dataclass
class DocumentRegistry:
    """Registry for Document classes."""

    django_model_map: Dict[Type[models.Model], Type["Document"]]
    related_django_model_map: Dict[Type[models.Model], Set[Type["Document"]]]

    def __init__(self) -> None:
        """Initialize the registry."""
        self.django_model_map = {}
        self.related_django_model_map = defaultdict(set)

    def register(self, document_class: Type["Document"]) -> None:
        """Register a Document class."""
        if document_class._django.model:
            self.django_model_map[document_class._django.model] = document_class

        for related_model in document_class._django.related_models:
            self.related_django_model_map[related_model].add(document_class)

    def update_document(
        self, model: Type[models.Model], model_object: models.Model, create: bool = True
    ) -> None:
        """Update document of specific model."""
        document_class = self.django_model_map.get(model)

        # Check if Auto Index is turned off for this specific Document.
        if document_class and document_class._django.auto_index:
            # Try to Update the Document if not object is created.
            document_class.update_from_model_instance(model_object, create=create)

    def update_related_documents(
        self,
        model: Type[models.Model],
        model_object: models.Model,
        exclude: models.Model = None,
    ) -> None:
        """Update related documents of a specific model."""
        document_classes = self.related_django_model_map.get(model, set())

        # Update the related Documents if any.
        for document_class in document_classes:
            if not document_class._django.auto_index:
                continue

            document_class.update_from_related_model_instance(
                model_object, exclude=exclude
            )

    def remove_document(
        self, model: Type[models.Model], model_object: models.Model
    ) -> None:
        """Remove document of a specific model."""
        document_class = self.django_model_map.get(model)

        # Check if Auto Index is turned off for this specific Document.
        if document_class and document_class._django.auto_index:
            # Try to Delete the Document from Redis Index.
            document_class.delete(model_object.pk)


document_registry: DocumentRegistry = DocumentRegistry()
