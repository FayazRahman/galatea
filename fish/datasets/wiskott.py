#!/usr/bin/env python

import functools
import warnings
import sys, os, logging
import glob
_logger = logging.getLogger(__name__)

import pdb
import numpy as np

import theano

from pylearn2.datasets import dense_design_matrix, Dataset
from pylearn2.datasets.dense_design_matrix import DenseDesignMatrix, DefaultViewConverter
from pylearn2.expr.preprocessing import global_contrast_normalize
from pylearn2.utils import image, string_utils, serial
from pylearn2.space import CompositeSpace, Conv2DSpace
from videodaset import VideoDataset

class WiskottVideoConfig(object):
    '''This is just a container for specifications for a WiskottVideo
    dataset. This allows one to easily create train/valid/test datasets
    with identical configuration using anchors in YAML files.
    '''

    def __init__(self, axes = ('b', 0, 1, 2, 'c'),
                 num_frames = 3, height = 32, width = 32, num_channels = 1):
        # Arbitrary choice: we do the validation here, not in WiskottVideo
        assert isinstance(axes, tuple), 'axes must be a tuple'
        self.axes = axes
        assert num_frames > 0, 'num_frames must be positive'
        self.num_frames = num_frames
        assert height > 0, 'height must be positive'
        self.height = height
        assert width > 0, 'width must be positive'
        self.width = width
        assert num_channels == 1, 'only 1 channel is supported for now'
        self.num_channels = num_channels

class WiskottVideo(Dataset):
    '''Data from "Invariant Object Recognition and Pose Estimation with Slow
    Feature Analysis"
    '''

    _default_seed = (17, 2, 946)

    dirs_train = ['fish_layer0_15_standard',
                  'spheres_layer0_5_standard',
                  'fish_layer2_15_standard',
                  'spheres_layer2_5_standard']

    dirs_test = ['fish_test_25_standard',
                 'spheres_test_10_standard']

    def __init__(self, which_set, config, quick = False):
        '''Create a WiskottVideo instance'''

        assert which_set in ('train', 'test')
        self.which_set = which_set
        assert isinstance(quick, bool), 'quick must be a bool'
        self.quick = quick
        if self.quick:
            print 'WARNING: quick mode, loading only a few data files.'

        # Copy main config from provided config
        self.axes            = config.axes
        self.num_frames      = config.num_frames
        self.height          = config.height
        self.width           = config.width
        self.num_channels    = config.num_channels

        # Load data into memory
        feature_regex = 'seq_0[0-9][0-9][0-9].zip.npy'
        label_regex   = 'seq_0[0-9][0-9][0-9].zip.labels.npy'
        dirs = self.dirs_test if self.which_set == 'test' else self.dirs_train

        # A list of data matrices, one per short video of ~200 frames
        #   Example: self._feature_matrices[0].shape: (156, 156, 200)
        self._feature_matrices = self._load_data(dirs, feature_regex)
        # A list of label matrices, one per short video of ~200 frames
        #   Example: self._label_matrices[0].shape: (200, 77)
        ####self._label_matrices = self._load_data(dirs, label_regex)
        #   Example: self._label_matrices[0].shape: (200, 29)
        self._label_matrices = self._load_data(dirs, label_regex, is_labels=True)

        assert len(self._feature_matrices) == len(self._label_matrices)
        self._n_matrices = len(self._feature_matrices)

    def _load_data(self, data_directories, file_regex, is_labels=False):
        filenames = []
        for data_directory in data_directories:
            file_filter = os.path.join(
                string_utils.preprocess('${PYLEARN2_DATA_PATH}'),
                'wiskott', data_directory, 'views',
                file_regex)
            filenames.extend(sorted(glob.glob(file_filter)))

        matrices = []
        if self.quick:
            filenames = filenames[:3]
        print 'Loading data from %d files:      ' % len(filenames),
        for ii, filename in enumerate(filenames):
            if is_labels:
                assert ('fish' in filename) or ('spheres' in filename), 'Not sure if fish or spheres.'
                is_fish = 'fish' in filename
                matrices.append(load_labels(filename, is_fish))
            else:
                matrices.append(serial.load(filename))
            print '\b\b\b\b\b\b%5d' % (ii+1),
            sys.stdout.flush()
        print
        return matrices


    @functools.wraps(Dataset.iterator)
    def OLD_iterator(self, mode=None, batch_size=None, num_batches=None,
                 topo=None, targets=None, rng=None, data_specs=None,
                 return_tuple=False, ignore_data_specs=False):

        if batch_size is None: batch_size = 100
        if num_batches is None: num_batches = 100
        assert batch_size > 0
        assert num_batches > 0
        assert topo is None
        assert targets is None

        if mode is None: mode = 'shuffled_sequential'
        assert mode in ('sequential', 'shuffled_sequential'), (
            'Mode must be one of: sequential, shuffled_sequential'
        )
        if mode != 'shuffled_sequential':
            warnings.warn('billiard dataset returning its only supported iterator type -- shuffled -- despite the request to the contrary')
        if not ignore_data_specs:
            assert data_specs != None, 'Must provide data_specs'
            assert len(data_specs) == 2, 'data_specs must include only one tuple for "features"'
            assert type(data_specs[0]) is CompositeSpace, 'must be composite space...??'
            assert data_specs[0].num_components == 1, 'must only have one component, features'
            assert data_specs[1][0] == 'features', (
                'data_specs must include only one tuple for "features"'
            )

        #underlying_dataspecs = (self._output_space, 'features')
        underlying_space = Conv2DSpace((self.height, self.width),
                                       num_channels = self.num_channels)
        underlying_dataspecs = (underlying_space, 'features')

        self._underlying_iterator = self._dense_design_matrix.iterator(
            mode = 'random_slice',     # IMPORTANT: to return contiguous slices representing chunks of time!
            batch_size = self.num_frames,
            num_batches = num_batches * batch_size,
            rng=rng,
            data_specs=underlying_dataspecs,
            return_tuple=False
        )

        #pdb.set_trace()

        return CopyingConcatenatingIterator(
            self._underlying_iterator,
            num_concat = batch_size,
            return_tuple = return_tuple
        )

def load_labels(path, is_fish):
    """
    path to a numpy file containing the labels
    is_fish: bool, True=fish, False=spheres

    numpy file has this format:
        x
        y
        one-hot encoding of label (25 elements for fish, 10 for spheres)
        sin(phi_y)         (25 elements for fish, 10 for spheres)
        cos(phi_y)        (25 elements for fish, 10 for spheres)
        sin(phi_z)         (not present for fish, they only rotate around 1
                axis, 10 elements for spheres)
        cos(phi_z)        (not present for fish, they only rotate around 1
                axis, 10 elements for spheres)

    This function loads the numpy file, collapses sin(phi_y) into one column,
    cos(phi_y) into one column, sin(phi_z) into one column, and cos(phi_z) into
    one column. It then returns data with this format:

    id (one hot)
    x
    y
    sin(phi_y)
    cos(phi_y)
    sin(phi_z)
    cos(phi_z)
    """

    raw = np.load(path)

    if is_fish:
        assert raw.shape[1] == 77
    else:
        assert raw.shape[1] == 52

    num_feat = 16
    num_id = 10
    if is_fish:
        num_feat = 29
        num_id = 25

    batch_size = raw.shape[0]

    rval = np.zeros((batch_size, num_feat), dtype=raw.dtype)

    raw_start = 2
    ids = raw[:, raw_start:raw_start + num_id]
    raw_start += num_id
    rval[:, 0:num_id] = ids                            # IDs
    rval_start = num_id
    rval[:, rval_start:rval_start + 2] = raw[:, 0:2]   # x,y
    rval_start += 2
    for i in xrange(2 + (1 - is_fish) * 2):
        #raw[:, rval_start] = (ids * raw[raw_start:raw_start+num_id]).sum(axis=1)
        rval[:,rval_start] = raw[:,raw_start]
        rval_start += 1
        raw_start += num_id

    assert raw_start == raw.shape[1]
    assert rval_start == rval.shape[1]

    return rval

class WiskottVideo2(VideoDataset):

    def __init__(self, config):
        data =  WiskottVideo('train', config, quick = True)
        self.data = (data._feature_matrices, data._label_matrices)
        print 'TODO'
        return
        self.space = CompositeSpace((
            Conv3DSpace(TODO),
            VectorSpace(dim = TODO)))
        self.source = ('features', 'labels')
        self.data_specs = (self.space, self.soruce)

def demo():
    num_frames = 300
    height = 10
    width  = 10

    config = WiskottVideoConfig(
        num_frames = num_frames,
        height = height,
        width  = width,
        )

    #wisk = WiskottVideo2('train', config, quick = True)
    wisk = WiskottVideo2(config)
    pdb.set_trace()

    it = wisk.iterator()

    print 'done.'
    pdb .set_trace()


if __name__ == '__main__':
    demo()
