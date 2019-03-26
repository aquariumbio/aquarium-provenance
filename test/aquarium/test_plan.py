import pytest
from aquarium.provenance import (
    ItemEntity, OperationActivity, OperationInput, ProvenanceTrace)


def create_operation(id):
    return OperationActivity(id=id, operation_type=None)


def create_item(id):
    return ItemEntity(item_id=id, sample=None, object_type=None)


def create_input(item):
    return OperationInput(name="InArg#{item.item_id}", field_value_id=1,
                          item_entity=item)


@pytest.fixture(scope="module")
def simple_plan():

    plan = ProvenanceTrace(experiment_id="simple")
    op_activity = create_operation("op1")
    item = create_item("item1")
    plan.add_item(item)
    input = create_input(item)
    op_activity.add_input(input)
    plan.add_operation(op_activity)
    return plan


class TestProvenanceTrace:

    def test_simple_plan(self, simple_plan):
        """
        uses(op1, item_item1)
        """
        plan = simple_plan
        input_list = plan.get_inputs()
        assert len(input_list) == 1
        item = next(iter(input_list))
        assert item.item_id == 'item1'

    def test_plan(self, simple_plan):

        plan = simple_plan
        op_activity = create_operation('op_external')
        item1 = plan.get_item('item1')
        item1.add_generator(op_activity)
        """
        uses(op1, item1)
        generates(op_external, item1)
        """
        input_list = plan.get_inputs()
        assert len(input_list) == 1
        item = next(iter(input_list))
        assert item.item_id == 'item1'

        """
        uses(op1, input_item1)
        generates(op_external, input_item1)
        derived_from(item1, item2)
        """
        item2 = create_item('item2')
        item1.add_source(item2)
        input_list = plan.get_inputs()
        assert len(input_list) == 1
        item = next(iter(input_list))
        assert item.item_id == 'item1'

        plan.add_item(item2)
        input_list = plan.get_inputs()
        assert len(input_list) == 1
        item = next(iter(input_list))
        assert item.item_id == 'item2'
