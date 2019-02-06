from aquarium.provenance import (PlanTrace, ItemEntity, OperationActivity)
from aquarium.trace.visitor import ProvenanceVisitor
from sbol import Document, setHomespace


class SBOLVisitor(ProvenanceVisitor):
    """
    A visitor to convert aquarium.provenance.PlanTrace to an SBOL Document
    object containing item and operation linkages.

    Apply the visitor to a trace and then access the `doc` property.

    Does not currently handle jobs as generators, or files generated as 
    measurements.
    """

    def __init__(self, *, namespace: str, trace: PlanTrace = None):
        # TODO: is it sufficient for homespace to be set only for document init?
        setHomespace(namespace)
        self.doc = Document()
        super().__init__(trace)

    def visit_item(self, item: ItemEntity):
        component = self._get_component(item)

        if item.generator is None:
            return
        if item.generator.is_job():
            # TODO: handle jobs as generator
            return

        activity = self._get_activity(item.generator)
        component.wasGeneratedBy = activity

    def visit_operation(self, operation: OperationActivity):
        """
        Adds an SBOL activity for the given OperationActivity, and sets
        usage for all ItemEntity objects that are inputs to the operation.

        Creates any object that does not exist in self.doc.
        """
        activity = self._get_activity(operation)
        for item in operation.get_input_items():
            component = self._get_component(item)
            usage = activity.usages.create("usage_{}".format(item.item_id))
            usage.entity = component.identity

    def _get_activity(self, activity):
        """
        Returns an SBOL activity for the Aquarium activity.
        Creates the object if it does not exist.
        """
        if activity.is_job():
            activity_name = "job_{}".format(activity.job_id)
        else:
            activity_name = "operation_{}".format(activity.operation_id)
        if activity_name in self.doc.activities:
            return self.doc.activities[activity_name]
        return self.doc.activities.create(activity_name)

    def _get_component(self, item: ItemEntity):
        """
        Returns an SBOL component for the given ItemEntity.
        Creates the object if it does not exist.
        """
        component_name = "item_{}".format(item.item_id)
        if component_name in self.doc.componentDefinitions:
            return self.doc.componentDefinitions[component_name]
        return self.doc.componentDefinitions.create(component_name)
