import pytest
from aquarium.provenance import (
    AttributesMixin,
    CollectionEntity, ItemEntity, PartEntity,
    FileEntity, ExternalFileEntity,
    MissingEntity
)


def create_item(id):
    return ItemEntity(item_id=id, sample=None, object_type=None)


def create_collection(id):
    return CollectionEntity(item_id=id, object_type=None)


def create_part(id):
    dummy_id = "dummy_collection_{}".format(id)
    return PartEntity(
        part_id=id, part_ref='blah/blah', collection=create_collection(dummy_id))


class TestAttributes:

    def test_none(self):
        item1 = create_item("item1")
        assert isinstance(item1, AttributesMixin)
        assert not item1.has_attribute('key')
        assert item1.get_attribute('key') is None

    def test_one(self):
        item1 = create_item("item1")
        assert not item1.has_attribute('key')
        item1.add_attribute({'key': 'blah'})
        assert item1.has_attribute('key')
        assert item1.get_attribute('key') is 'blah'

    def test_equality(self):
        item1 = create_item("item1")
        assert item1 != 'blah'
        item2 = create_item('item1')
        item1.add_attribute({'key': 'blah'})
        item2.add_attribute({'key': 'blah'})
        assert item1 == item2  # relies on ids being the same


class TestHashableEntity:

    def test_hashable(self):
        "check that all entities are hashable"
        the_set = set()
        the_set.add(create_item("item1"))
        the_set.add(create_collection("coll2"))
        the_set.add(create_part('part3'))

# TODO: check files; punting for now
