import logging
import re
from aquarium.provenance import (CollectionEntity, PartEntity)
from aquarium.trace.visitor import ProvenanceVisitor
from util.plate import well_coordinates, coordinates_for
from collections.abc import Mapping


class AddPartsVisitor(ProvenanceVisitor):
    def __init__(self, trace=None):
        self.factory = None
        self.part_map = dict()  # part ref string -> part_entity
        super().__init__(trace)

    def add_factory(self, factory):
        self.factory = factory

    def visit_collection(self, collection: CollectionEntity):
        """
        Adds the parts for a collection.
        """
        if collection.has_parts():
            return
        logging.debug("Adding parts for collection %s", collection.item_id)
        item = self.factory.item_map[collection.item_id]
        self._collect_parts(item)

        upload_matrix = AddPartsVisitor.get_upload_matrix(collection)
        routing_matrix = AddPartsVisitor.get_routing_matrix(collection)
        self._create_parts(collection, upload_matrix, routing_matrix)

    def visit_part(self, part_entity: PartEntity):
        if part_entity.sources:
            return

        source_list = part_entity.get_attribute('source')
        if not source_list:
            return

        logging.debug("Adding sources for part %s", part_entity.item_id)
        for src_obj in source_list:
            source_entity = None
            source_id = str(src_obj['id'])
            if 'row' in src_obj:  # is a part
                row = src_obj['row']
                col = src_obj['column']
                ref = AddPartsVisitor.get_part_ref(
                    collection_id=source_id,
                    well=well_coordinates(row, col))
                if ref in self.part_map:
                    source_entity = self.part_map[ref]
            else:
                source_entity = self.trace.get_item(source_id)
            if source_entity:
                part_entity.add_source(source_entity)

    @staticmethod
    def get_part_ref(*, collection_id, well):
        return "{}/{}".format(collection_id, well)

    def _collect_parts(self, item):
        for part_association in item.part_associations:
            logging.debug("Getting part %s", part_association.part_id)
            if self.trace.has_item(part_association.part_id):
                return self.trace.get_item(part_association.part_id)

            if not self.trace.has_item(part_association.collection_id):
                logging.error("Collection %s for part %s not in trace",
                              part_association.part_id,
                              part_association.collection_id)
                return None

            collection = self.trace.get_item(part_association.collection_id)
            ref = AddPartsVisitor.get_part_ref(
                collection_id=collection.item_id,
                well=well_coordinates(
                    part_association.row,
                    part_association.column)
            )
            part = part_association.part
            part_entity = PartEntity(part_id=part_association.part_id,
                                     part_ref=ref,
                                     sample=part.sample,
                                     object_type=part.object_type,
                                     collection=collection)
            self.part_map[part_entity.ref] = part_entity
            self.trace.add_item(part_entity)
            self.factory.item_map[part_entity.item_id] = part

    def _create_parts(self, collection, upload_matrix, routing_matrix):
        self._create_parts_from_samples(collection)
        if routing_matrix:
            self._create_parts_from_routing(collection, routing_matrix)
        if upload_matrix:
            self._create_parts_from_uploads(collection, upload_matrix)

    def _create_parts_from_samples(self, coll_entity):
        collection = self.factory.item_map[coll_entity.item_id]
        generator = coll_entity.generator
        item_id = coll_entity.item_id
        for i in range(len(collection.matrix)):
            row = collection.matrix[i]
            for j in range(len(row)):
                sample = self.factory.get_sample(row[j])
                if not sample:
                    continue

                part_ref = AddPartsVisitor.get_part_ref(
                    collection_id=item_id,
                    well=well_coordinates(i, j))
                part_entity = self._get_part(part_ref=part_ref,
                                             collection=coll_entity)
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

                part_ref = AddPartsVisitor.get_part_ref(
                    collection_id=entity.item_id,
                    well=well_coordinates(i, j))
                part_entity = self._get_part(part_ref=part_ref,
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

                part_ref = AddPartsVisitor.get_part_ref(
                    collection_id=entity.item_id,
                    well=well_coordinates(i, j))
                part_entity = self._get_part(part_ref=part_ref,
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
        - item_id/well
        - object_type_name/item_id/sample_id/well
        The latter form is used in cases where the item is not a collection,
        but consists of subparts that are not explicitly modeled.
        An example is a yeast plate with colonies.
        In this case, return the item.

        Some plans have a well of the form [[i,j]] that needs to be
        converted to alphanumeric form.

        This should not be necessary once part are first order in aquarium.
        """
        logging.debug("Getting entity for source_id %s", source_id)
        if self.trace.has_item(source_id):
            return self.trace.get_item(source_id)

        source_components = source_id.split('/')
        if re.match("[0-9]+", source_id):
            source_item_id = source_components[0]
            well = None
            if len(source_components) == 2:
                well = source_components[1]
                # fix stray numeric coordinates
                pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
                match = re.match(pattern, well)
                if match:
                    well = well_coordinates(
                        int(match[1]), int(match[2]))
                    part_ref = AddPartsVisitor.get_part_ref(
                        collection_id=source_item_id,
                        well=well)
                    if part_ref in self.part_map:
                        return self.part_map[part_ref]
                # TODO: handle bad part ref
        elif len(source_components) == 4:
            # TODO: check this is an identifier
            source_item_id = source_components[1]
            well = source_components[3]
        else:
            # TODO: raise exception here since id is malformed
            msg = "unrecognized source ID: %s"
            logging.warning(msg, source_id)
            return None

        source_item_entity = self.factory.get_item(item_id=source_item_id)

        if not well:
            logging.debug("No well, returning %s %s as source",
                          source_item_entity.item_type,
                          source_item_entity.item_id)
            return source_item_entity

        if not source_item_entity.is_collection():
            msg = "ignoring part %s from non-collection %s in source"
            logging.info(msg, well, source_item_id)
            return source_item_entity

        part_ref = AddPartsVisitor.get_part_ref(collection_id=source_item_id,
                                                well=well)
        logging.debug("Part reference %s", part_ref)

        # this assumes part_ref is well-formed
        source_part_entity = self._get_part(part_ref=part_ref,
                                            collection=source_item_entity)
        if not source_part_entity.sample:
            source_collection = self.factory.item_map[source_item_id]
            (i, j) = AddPartsVisitor._split_well_coordinate(well)
            sample_id = source_collection.matrix[i][j]
            sample = self.factory.get_sample(sample_id)
            source_part_entity.sample = sample

        return source_part_entity

    def _get_part(self, *, part_ref, collection=None):
        logging.debug("Getting part %s", part_ref)
        if part_ref in self.part_map:
            return self.part_map[part_ref]
        if self.trace.has_item(part_ref):
            return self.trace.get_item(part_ref)

        if not collection:
            logging.error("No collection given for new part %s", part_ref)
            # TODO: throw exception instead
            return None

        part_entity = PartEntity(part_id=part_ref, part_ref=part_ref,
                                 collection=collection)
        self.trace.add_item(part_entity)
        return part_entity

    @staticmethod
    def _split_well_coordinate(well):
        pattern = r"([A-Z])([0-9]+)"
        match = re.match(pattern, well)
        if match:
            return coordinates_for(well)
        pattern = r"\[\[([0-9]+),[ \t]*([0-9]+)\]\]"
        match = re.match(pattern, well)
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
        """
        Newer protocols have the key in all uppercase.
        """
        upload_attribute = entity.get_attribute('SAMPLE_UPLOADs')
        if upload_attribute:
            return upload_attribute['upload_matrix']

        upload_attribute = entity.get_attribute('SAMPLE_uploads')
        if not upload_attribute:
            return None

        upload_list = sorted(upload_attribute,
                             key=lambda upload: upload['upload_file_name'])

        upload_matrix = list()
        count = 0
        row = list()
        for upload in upload_list:
            row.append(upload['id'])
            count += 1
            if count % 12 == 0:
                upload_matrix.append(row)
                row = list()
        if count % 12 != 0:
            upload_matrix.append(row)

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
