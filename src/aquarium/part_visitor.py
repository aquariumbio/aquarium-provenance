import logging
import re
from aquarium.provenance import (CollectionEntity, PartEntity)
from aquarium.trace_visitor import ProvenanceVisitor
from util.plate import well_coordinates, coordinates_for
from collections.abc import Mapping


class AddPartsVisitor(ProvenanceVisitor):
    def __init__(self, trace=None):
        self.factory = None
        super().__init__(trace)

    def add_factory(self, factory):
        self.factory = factory

    def visit_collection(self, collection: CollectionEntity):
        if collection.parts:
            return
        logging.debug("Adding parts for collection %s", collection.item_id)
        upload_matrix = AddPartsVisitor.get_upload_matrix(collection)
        routing_matrix = AddPartsVisitor.get_routing_matrix(collection)
        self._create_parts(collection, upload_matrix, routing_matrix)

    def _create_parts(self, entity, upload_matrix, routing_matrix):
        self._create_parts_from_samples(entity)
        if routing_matrix:
            self._create_parts_from_routing(entity, routing_matrix)
        if upload_matrix:
            self._create_parts_from_uploads(entity, upload_matrix)

    def _create_parts_from_samples(self, entity):
        collection = entity.collection
        generator = entity.generator
        item_id = entity.item_id
        for i in range(len(collection.matrix)):
            row = collection.matrix[i]
            for j in range(len(row)):
                sample = self.factory.get_sample(row[j])
                if not sample:
                    continue

                part_id = str(item_id) + '/' + well_coordinates(i, j)
                part_entity = self._get_part(part_id=part_id,
                                             collection=entity)
                if generator and not part_entity.generator:
                    part_entity.add_generator(generator)

                if not part_entity.sample:
                    msg = "Adding sample %s to part %s"
                    logging.debug(msg, sample.id, part_entity.item_id)
                    part_entity.sample = sample

    def _create_parts_from_routing(self, entity, routing_matrix):
        for i in range(len(routing_matrix)):
            row = routing_matrix[i]
            for j in range(len(row)):
                routing_entry = row[j]
                if not routing_entry or not isinstance(routing_entry, Mapping):
                    continue

                source_id = AddPartsVisitor._get_source_id(routing_entry)

                if not source_id:
                    continue

                part_id = str(entity.item_id) + '/' + well_coordinates(i, j)
                part_entity = self._get_part(part_id=part_id,
                                             collection=entity)
                if entity.generator and not part_entity.generator:
                    part_entity.add_generator(entity.generator)

                source_entity = self._get_source(source_id)
                if source_entity:
                    if source_entity.sample:
                        if not part_entity.sample:
                            msg = "Adding sample %s to part %s"
                            logging.debug(msg, source_entity.sample.id,
                                          part_entity.item_id)
                            part_entity.sample = source_entity.sample
                        elif source_entity.sample.id != part_entity.sample.id:
                            msg = "Source %s sample %s does not match " \
                                "part %s sample %s"
                            logging.error(msg, source_id,
                                          source_entity.sample.id,
                                          part_entity.item_id,
                                          part_entity.sample.id)
                            continue

                    part_entity.add_source(source_entity)
                    msg = "Adding %s %s as source for %s %s for ref %s"
                    logging.debug(msg, source_entity.item_type,
                                  source_entity.item_id,
                                  part_entity.item_type,
                                  part_entity.item_id,
                                  source_id)
                    if source_entity.is_item():
                        logging.debug(
                            "source %s %s is an item",
                            source_entity.item_type, source_entity.item_id)
                        part_entity.add_attribute(
                            {'source_reference': source_id})

                attributes = AddPartsVisitor._get_attributes(routing_entry)
                part_entity.add_attribute(attributes)

    def _create_parts_from_uploads(self, entity, upload_matrix):
        for i in range(len(upload_matrix)):
            row = upload_matrix[i]
            for j in range(len(row)):
                upload_id = row[j]

                if not upload_id or upload_id <= 0:
                    continue

                part_id = str(entity.item_id) + '/' + well_coordinates(i, j)
                part_entity = self._get_part(part_id=part_id,
                                             collection=entity)
                if entity.generator and not part_entity.generator:
                    part_entity.add_generator(entity.generator)

                msg = "Part %s has upload %s"
                logging.debug(msg, part_entity.item_id, upload_id)
                file_entity = self.factory.get_file(upload_id=upload_id)
                if file_entity:
                    file_entity.add_source(part_entity)

    def _get_source(self, source_id):
        """
        Returns an entity for the ID, creating the entity if it does not
        already exist.

        Source IDs come from data associations of collections and indicate the
        source for a part.

        May have one of the forms
        - item_id
        - item_id/part_ref
        - object_type_name/item_id/sample_id/part_ref
        The latter form is used in cases where the item is not a collection,
        but consists of subparts that are not explicitly modeled.
        An example is a yeast plate with colonies.
        In this case, return the item.

        Some plans have a part_ref of the form [[i,j]] that needs to be
        converted to alphanumeric form.

        This should not be necessary once part are first order in aquarium.
        """
        if self.trace.has_item(source_id):
            return self.trace.get_item(source_id)

        source_components = source_id.split('/')
        if re.match("[0-9]+", source_id):
            source_item_id = source_components[0]
            part_ref = None
            if len(source_components) == 2:
                part_ref = source_components[1]
                # fix stray numeric coordinates
                pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
                match = re.match(pattern, part_ref)
                if match:
                    part_ref = well_coordinates(
                        int(match[1]), int(match[2]))
                    new_id = source_item_id + '/' + part_ref
                    if self.trace.has_item(new_id):
                        return self.trace.get_item(new_id)
                # TODO: handle bad part ref
        elif len(source_components) == 4:
            # TODO: check this is an identifier
            source_item_id = source_components[1]
            part_ref = source_components[3]
        else:
            # TODO: raise exception here since id is malformed
            msg = "unrecognized source ID: %s"
            logging.warning(msg, source_id)
            return None

        source_item_entity = self.factory.get_item(item_id=source_item_id)

        if not part_ref:
            return source_item_entity

        if not source_item_entity.is_collection():
            msg = "ignoring part %s from non-collection %s in source"
            logging.info(msg, part_ref, source_item_id)
            return source_item_entity

        part_id = source_item_id + '/' + part_ref

        # this assumes part_ref is well-formed
        (i, j) = AddPartsVisitor._split_well_coordinate(part_ref)
        sample_id = source_item_entity.collection.matrix[i][j]
        sample = self.factory.get_sample(sample_id)
        source_part_entity = self._get_part(
            part_id=part_id, collection=source_item_entity)
        if not source_part_entity.sample:
            source_part_entity.sample = sample
        return source_part_entity

    def _get_part(self, *, part_id, collection=None):
        logging.debug("Getting part %s", part_id)
        if self.trace.has_item(part_id):
            return self.trace.get_item(part_id)

        if not collection:
            logging.error("No collection given for new part %s", part_id)
            # TODO: throw exception instead
            return None

        part_entity = PartEntity(part_id=part_id, collection=collection)
        self.trace.add_item(part_entity)
        return part_entity

    @staticmethod
    def _split_well_coordinate(part_ref):
        pattern = r"([A-Z])([0-9]+)"
        match = re.match(pattern, part_ref)
        if match:
            return coordinates_for(part_ref)
        pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
        match = re.match(pattern, part_ref)
        if match:
            return (int(match[1]), int(match[2]))

    @staticmethod
    def _get_source_id(entry):
        if 'source' in entry:
            source = entry['source']
            if isinstance(source, list):
                return str(source[0]['id'])
            else:
                return str(entry['source'])
        return None

    @staticmethod
    def _get_routing_entry(routing_matrix, i, j):
        if not routing_matrix:
            return dict()
        entry = routing_matrix[i][j]
        if entry and isinstance(entry, Mapping):
            return entry
        return dict()

    @staticmethod
    def get_upload_matrix(entity):
        upload_matrix = None
        upload_attribute = entity.get_attribute('SAMPLE_UPLOADs')
        if upload_attribute:
            upload_matrix = upload_attribute['upload_matrix']
        return upload_matrix

    @staticmethod
    def get_routing_matrix(entity):
        routing_matrix = None
        if entity.has_attribute('routing_matrix'):
            logging.debug("%s %s has routing matrix",
                          entity.item_type, entity.item_id)
            routing_attribute = entity.get_attribute('routing_matrix')
            routing_matrix = routing_attribute['rows']
        elif entity.has_attribute('routing_dilution_matrix'):
            logging.debug("%s %s has routing dilution matrix",
                          entity.item_type, entity.item_id)
            routing_attribute = entity.get_attribute('routing_dilution_matrix')
            routing_matrix = routing_attribute['rows']
        elif entity.has_attribute('part_data'):
            logging.debug("%s %s has part_data",
                          entity.item_type, entity.item_id)
            routing_matrix = entity.get_attribute('part_data')
        return routing_matrix

    @staticmethod
    def _get_attributes(entry):
        if 'attributes' not in entry:
            return dict()
        return entry['attributes']
