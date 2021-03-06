import abc
import numpy

from collections import defaultdict

from smqtk.representation import SmqtkRepresentation
from smqtk.utils.dict import merge_dict
from smqtk.utils.plugin import Pluggable
from smqtk.utils.parallel import parallel_map

from ._io import elements_to_matrix


def _uuid_and_vector_from_descriptor(descriptor):
    """
    Given a descriptor, return a tuple containing the UUID and associated
    vector for that descriptor

    :param descriptor: The descriptor to process.
    :type descriptor: smqtk.representation.descriptor_element.DescriptorElement
    :return: Tuple containing the UUID and associated vector for the given
        descriptor
    :rtype: tuple[collections.Hashable, numpy.ndarray]
    """
    return (descriptor.uuid(), descriptor.vector())


class DescriptorElement (SmqtkRepresentation, Pluggable):
    """
    Abstract descriptor vector container.

    This structure supports implementations that cache descriptor vectors on a
    per-UUID basis.

    UUIDs must maintain unique-ness when transformed into a string.

    Descriptor element equality based on shared descriptor type and vector
    equality. Two descriptor vectors that are generated by different types of
    descriptor generator should not be considered the same (though, this may be
    up for discussion).

    Stored vectors should be effectively immutable.

    """

    def __init__(self, type_str, uuid):
        """
        Initialize a new descriptor element.

        :param type_str: Type of descriptor. This is usually the name of the
            content descriptor that generated this vector.
        :type type_str: str

        :param uuid: Unique ID reference of the descriptor.
        :type uuid: collections.Hashable

        """
        super(DescriptorElement, self).__init__()

        self._type_label = type_str
        self._uuid = uuid

    def __hash__(self):
        return hash(self.uuid())

    def __eq__(self, other):
        if isinstance(other, DescriptorElement):
            return numpy.array_equal(self.vector(), other.vector())
        return False

    def __ne__(self, other):
        return not (self == other)

    def __repr__(self):
        return "%s{type: %s, uuid: %s}" % (self.__class__.__name__, self.type(),
                                           self.uuid())

    def __getstate__(self):
        return {
            "_type_label": self._type_label,
            "_uuid": self._uuid,
        }

    def __setstate__(self, state):
        self._type_label = state['_type_label']
        self._uuid = state['_uuid']

    @classmethod
    def get_default_config(cls):
        """
        Generate and return a default configuration dictionary for this class.
        This will be primarily used for generating what the configuration
        dictionary would look like for this class without instantiating it.

        By default, we observe what this class's constructor takes as arguments,
        aside from the first two assumed positional arguments, turning those
        argument names into configuration dictionary keys.
        If any of those arguments have defaults, we will add those values into
        the configuration dictionary appropriately.
        The dictionary returned should only contain JSON compliant value types.

        It is not be guaranteed that the configuration dictionary returned
        from this method is valid for construction of an instance of this class.

        :return: Default configuration dictionary for the class.
        :rtype: dict

        """
        # similar to parent impl, except we remove the ``type_str`` and ``uuid``
        # configuration parameters as they are to be specified at runtime.
        dc = super(DescriptorElement, cls).get_default_config()
        # These parameters must be specified at construction time.
        del dc['type_str'], dc['uuid']
        return dc

    # noinspection PyMethodOverriding
    @classmethod
    def from_config(cls, config_dict, type_str, uuid, merge_default=True):
        """
        Instantiate a new instance of this class given the desired type, uuid,
        and JSON-compliant configuration dictionary.

        :param type_str: Type of descriptor. This is usually the name of the
            content descriptor that generated this vector.
        :type type_str: str

        :param uuid: Unique ID reference of the descriptor.
        :type uuid: collections.Hashable

        :param config_dict: JSON compliant dictionary encapsulating
            a configuration.
        :type config_dict: dict

        :param merge_default: Merge the given configuration on top of the
            default provided by ``get_default_config``.
        :type merge_default: bool

        :return: Constructed instance from the provided config.
        :rtype: DescriptorElement

        """
        c = {}
        merge_dict(c, config_dict)
        c['type_str'] = type_str
        c['uuid'] = uuid
        return super(DescriptorElement, cls).from_config(c, merge_default)

    def uuid(self):
        """
        :return: Unique ID for this vector.
        :rtype: collections.Hashable
        """
        return self._uuid

    def type(self):
        """
        :return: Type label type of the DescriptorGenerator that generated this
            vector.
        :rtype: str
        """
        return self._type_label

    @classmethod
    def _get_many_vectors(cls, descriptors):
        """
        Internal method to be overridden by subclasses to return many vectors
        associated with given descriptors.

        :note: Returned vectors are *not* guaranteed to be returned in the
            order they are requested. Missing vectors may be returned as None
            or omitted entirely from results. The wrapper function
            `get_many_vectors` handles re-ordering as necessary and insertion
            of None for missing values.

        :param descriptors: Iterable of descriptors to query for.
        :type descriptors: collections.Iterable[
            smqtk.representation.descriptor_element.DescriptorElement]

        :return: Iterator of tuples containing the descriptor uuid and the
            vector associated with the given descriptors or None if the
            descriptor has no associated vector
        :rtype: collections.Iterable[
            tuple[collections.Hashable, Union[numpy.ndarray, None]]]
        """
        for uuid_vector_pair in parallel_map(
                _uuid_and_vector_from_descriptor, descriptors,
                name='retrieve_vectors'):
            yield uuid_vector_pair

    @classmethod
    def get_many_vectors(cls, descriptors):
        """
        Get an iterator over vectors associated with given descriptors.

        :note: Most subclasses should override internal method
            `_get_many_vectors` rather than this external wrapper function. If
            a subclass does override this classmethod, it is responsible for
            appropriately handling any valid DescriptorElement, regardless of
            subclass.

        :param descriptors: Iterable of descriptors to query for.
        :type descriptors: collections.Iterable[
            smqtk.representation.descriptor_element.DescriptorElement]

        :return: Iterable of vectors associated with the given descriptors or
            None if the descriptor has no associated vector. Results are
            returned in the order that descriptors were given.
        :rtype: collections.Iterable[Union[numpy.ndarray, None]]
        """
        batch_dictionary = defaultdict(list)
        uuid_indices = {}
        index = -1
        for index, descriptor_ in enumerate(descriptors):
            # Divide descriptors up into batches based on their type, since
            # each DescriptorElement subclass knows best how to optimally
            # retrieve vectors of its own type.
            batch_dictionary[type(descriptor_)].append(descriptor_)
            # Keep track of the order of descriptors to ensure that we return
            # vectors in the requested order after batching them out.
            uuid_indices[descriptor_.uuid()] = index

        # Default to None, since _get_many_vectors implementations can ignore
        # any descriptors that cannot be retrieved
        ordered_vectors = [None] * (index + 1)

        # Retrieve all the vectors for a given type of descriptor in a single
        # batch
        for cls, descriptor_batch in batch_dictionary.items():
            for uuid, vector in cls._get_many_vectors(descriptor_batch):
                ordered_vectors[uuid_indices[uuid]] = vector

        return ordered_vectors

    ###
    # Abstract methods
    #

    @abc.abstractmethod
    def has_vector(self):
        """
        :return: Whether or not this container current has a descriptor vector
            stored.
        :rtype: bool
        """

    @abc.abstractmethod
    def vector(self):
        """
        :return: Get the stored descriptor vector as a numpy array. This returns
            None of there is no vector stored in this container.
        :rtype: numpy.ndarray or None
        """

    @abc.abstractmethod
    def set_vector(self, new_vec):
        """
        Set the contained vector.

        If this container already stores a descriptor vector, this will
        overwrite it.

        :param new_vec: New vector to contain.
        :type new_vec: numpy.ndarray

        :returns: Self.
        :rtype: DescriptorMemoryElement

        """
