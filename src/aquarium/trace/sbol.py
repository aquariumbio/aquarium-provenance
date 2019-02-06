from aquarium.provenance import (PlanTrace, ItemEntity, OperationActivity)
from aquarium.trace.visitor import ProvenanceVisitor
from sbol import Document, setHomespace


class SBOLVisitor(ProvenanceVisitor):

    def __init__(self, *, namespace: str, trace: PlanTrace = None):
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
        activity = self._get_activity(operation)
        for item in operation.get_input_items():
            component = self._get_component(item)
            usage = activity.usages.create("usage_{}".format(item.item_id))
            usage.entity = component.identity

    def _get_activity(self, activity):
        if activity.is_job():
            activity_name = "job_{}".format(activity.job_id)
        else:
            activity_name = "operation_{}".format(activity.operation_id)
        if activity_name in self.doc.activities:
            return self.doc.activities[activity_name]
        return self.doc.activities.create(activity_name)

    def _get_component(self, item: ItemEntity):
        component_name = "item_{}".format(item.item_id)
        if component_name in self.doc.componentDefinitions:
            return self.doc.componentDefinitions[component_name]
        return self.doc.componentDefinitions.create(component_name)
