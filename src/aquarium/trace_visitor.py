import abc

from aquarium.provenance import (
    CollectionEntity,
    FileEntity,
    ItemEntity,
    OperationActivity,
    PartEntity,
    PlanTrace
)


class ProvenanceVisitor(abc.ABC):
    @abc.abstractmethod
    def __init__(self, trace=None):
        self.trace = trace

    def add_factory(self, factory):
        pass

    def add_trace(self, trace):
        self.trace = trace

    def visit_collection(self, collection: CollectionEntity):
        return

    def visit_file(self, file: FileEntity):
        return

    def visit_item(self, item: ItemEntity):
        return

    def visit_operation(self, operation: OperationActivity):
        return

    def visit_part(self, part: PartEntity):
        return

    def visit_plan(self, plan: PlanTrace):
        return


class BatchVisitor(ProvenanceVisitor):

    def __init__(self):
        self.visitors = list()
        super().__init__()

    def add_trace(self, trace):
        for visitor in self.visitors:
            visitor.add_trace(trace)
        super().add_trace(trace)

    def add_visitor(self, visitor):
        if self.trace:
            visitor.add_trace(self.trace)
        self.visitors.append(visitor)

    def visit_collection(self, collection):
        for visitor in self.visitors:
            collection.apply(visitor)

    def visit_item(self, item):
        for visitor in self.visitors:
            item.apply(visitor)

    def visit_part(self, part):
        for visitor in self.visitors:
            part.apply(visitor)

    def visit_file(self, file):
        for visitor in self.visitors:
            file.apply(visitor)

    def visit_plan(self, plan):
        for visitor in self.visitors:
            plan.apply(visitor)

    def visit_operation(self, operation):
        for visitor in self.visitors:
            operation.apply(visitor)


class FactoryVisitor(BatchVisitor):

    def __init__(self):
        self.factory = None
        super().__init__()

    def add_factory(self, factory):
        self.factory = factory
        for visitor in self.visitors:
            visitor.add_factory(self.factory)

    def add_visitor(self, visitor):
        if self.factory:
            visitor.add_factory(self.factory)
        super().add_visitor(visitor)
