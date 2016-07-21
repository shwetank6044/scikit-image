import sys
import base64

import numpy as np

if sys.version_info[0] == 3:
    base64decode = base64.decodebytes
else:
    base64decode = base64.decodestring

from . import _marching_cubes_lewiner_luts as mcluts
from . import _marching_cubes_lewiner_cy


def marching_cubes_lewiner(volume, level=None, spacing=(1., 1., 1.),
                           gradient_direction='descent', step_size=1,
                           allow_degenerate=True, use_classic=False):
    """
    Lewiner marching cubes algorithm to find surfaces in 3d volumetric data
    
    In contrast to ``marching_cubes()``, this algorithm resolves
    ambiguities and guarantees topologically correct results. Therefore,
    this algorithm generally a better choice, unless there is a specific
    need for the classic algorithm.
    
    Parameters
    ----------
    volume : (M, N, P) array
        Input data volume to find isosurfaces. Will internally be
        converted to float32 if necessary.
    level : float
        Contour value to search for isosurfaces in `volume`. If not
        given or None, the average of the min and max of vol is used.
    spacing : length-3 tuple of floats
        Voxel spacing in spatial dimensions corresponding to numpy array
        indexing dimensions (M, N, P) as in `volume`.
    gradient_direction : string
        Controls if the mesh was generated from an isosurface with gradient
        descent toward objects of interest (the default), or the opposite,
        considering the *left-hand* rule.
        The two options are:
        * descent : Object was greater than exterior
        * ascent : Exterior was greater than object
    step_size : int
        Step size in voxels. Default 1. Larger steps yield faster but
        coarser results. The result will always be topologically correct
        though.
    allow_degenerate : bool
        Whether to allow degenerate (i.e. zero-area) triangles in the
        end-result. Default True. If False, degenerate triangles are
        removed, at the cost of making the algorithm slower.
    use_classic : bool
        If given and True, the classic marching cubes by Lorensen (1987)
        is used. This option is included for reference purposes. Note
        that this algorithm has ambiguities and is not guaranteed to
        produce a topologically correct result. The results with using
        this option are *not* generally the same as the ``marching_cubes()``
        function.
    
    Returns
    -------
    verts : (V, 3) array
        Spatial coordinates for V unique mesh vertices. Coordinate order
        matches input `volume` (M, N, P).
    faces : (F, 3) array
        Define triangular faces via referencing vertex indices from ``verts``.
        This algorithm specifically outputs triangles, so each face has
        exactly three indices.
    normals : (V, 3) array
        The normal direction at each vertex, as calculated from the
        data.
    values : (V, ) array
        Gives a measure for the maximum value of the data in the local region
        near each vertex. This can be used by visualization tools to apply
        a colormap to the mesh.
    
    Notes
    -----
    The algorithm [1] is an improved version of Chernyaev's Marching
    Cubes 33 algorithm. It is an efficient algorithm that relies on
    heavy use of lookup tables to handle the many different cases,
    keeping the algorithm relatively easy. This implementation is
    written in Cython, ported from Lewiner's C++ implementation.
    
    References
    ----------
    ..  [1] Thomas Lewiner, Helio Lopes, Antonio Wilson Vieira and Geovan
            Tavares. Efficient implementation of Marching Cubes' cases with
            topological guarantees. Journal of Graphics Tools 8(2)
            pp. 1-15 (december 2003).
    
    See Also
    --------
    skimage.measure.marching_cubes
    skimage.measure.mesh_surface_area
    """ 
    
    # Check volume and ensure its in the format that the alg needs
    if not isinstance(volume, np.ndarray) or (volume.ndim != 3):
        raise ValueError('Input volume should be a 3D numpy array.')
    if volume.shape[0] < 2 or volume.shape[1] < 2 or volume.shape[2] < 2:
        raise ValueError("Input array must be at least 2x2x2.")
    volume = np.ascontiguousarray(volume, np.float32)  # no copy if not necessary
    
    # Check/convert other inputs:
    # level
    if level is None:
        level = 0.5 * (volume.min() + volume.max())
    else:
        level = float(level)
        if level < volume.min() or level > volume.max():
            raise ValueError("Surface level must be within volume data range.")
    # spacing
    if len(spacing) != 3:
        raise ValueError("`spacing` must consist of three floats.")
    # step_size
    step_size = int(step_size)
    if step_size < 1:
        raise ValueError('step_size must be at least one.')
    # use_classic
    use_classic = bool(use_classic)
    
    # Get LutProvider class (reuse if possible) 
    L = _get_mc_luts()
    
    # Apply algorithm
    func = _marching_cubes_lewiner_cy.marching_cubes
    vertices, faces , normals, values = func(volume, level, L, step_size, use_classic)
    
    if not len(vertices):
        raise RuntimeError('No surface found at the given iso value.')
    
    # Output in z-y-x order, as is common in skimage
    vertices = np.fliplr(vertices)
    normals = np.fliplr(normals)
    
    # Finishing touches to output
    faces.shape = -1, 3
    if gradient_direction == 'descent':
        # MC implementation is right-handed, but gradient_direction is left-handed
        faces = np.fliplr(faces)
    elif not gradient_direction == 'ascent':
        raise ValueError("Incorrect input %s in `gradient_direction`, see "
                         "docstring." % (gradient_direction))
    if spacing != (1, 1, 1):
        vertices = vertices * np.r_[spacing]
    
    if allow_degenerate:
        return vertices, faces, normals, values
    else:
        fun = _marching_cubes_lewiner_cy.remove_degenerate_faces
        return fun(vertices, faces, normals, values)


def _to_array(args):
    shape, text = args
    byts = base64decode(text.encode('utf-8'))
    ar = np.frombuffer(byts, dtype='int8')
    ar.shape = shape
    return ar


# Map an edge-index to two relative pixel positions. The ege index 
# represents a point that lies somewhere in between these pixels.
# Linear interpolation should be used to determine where it is exactly.
#   0
# 3   1   ->  0x
#   2         xx
EDGETORELATIVEPOSX = np.array([ [0,1],[1,1],[1,0],[0,0], [0,1],[1,1],[1,0],[0,0], [0,0],[1,1],[1,1],[0,0] ], 'int8')
EDGETORELATIVEPOSY = np.array([ [0,0],[0,1],[1,1],[1,0], [0,0],[0,1],[1,1],[1,0], [0,0],[0,0],[1,1],[1,1] ], 'int8')
EDGETORELATIVEPOSZ = np.array([ [0,0],[0,0],[0,0],[0,0], [1,1],[1,1],[1,1],[1,1], [0,1],[0,1],[0,1],[0,1] ], 'int8')


def _get_mc_luts():
    """ Kind of lazy obtaining of the luts.
    """ 
    if not hasattr(mcluts, 'THE_LUTS'):
        
        mcluts.THE_LUTS = _marching_cubes_lewiner_cy.LutProvider(
                EDGETORELATIVEPOSX, EDGETORELATIVEPOSY, EDGETORELATIVEPOSZ, 
                
                _to_array(mcluts.CASESCLASSIC), _to_array(mcluts.CASES),
                
                _to_array(mcluts.TILING1), _to_array(mcluts.TILING2), _to_array(mcluts.TILING3_1), _to_array(mcluts.TILING3_2), 
                _to_array(mcluts.TILING4_1), _to_array(mcluts.TILING4_2), _to_array(mcluts.TILING5), _to_array(mcluts.TILING6_1_1),
                _to_array(mcluts.TILING6_1_2), _to_array(mcluts.TILING6_2), _to_array(mcluts.TILING7_1), 
                _to_array(mcluts.TILING7_2), _to_array(mcluts.TILING7_3), _to_array(mcluts.TILING7_4_1), 
                _to_array(mcluts.TILING7_4_2), _to_array(mcluts.TILING8), _to_array(mcluts.TILING9), 
                _to_array(mcluts.TILING10_1_1), _to_array(mcluts.TILING10_1_1_), _to_array(mcluts.TILING10_1_2), 
                _to_array(mcluts.TILING10_2), _to_array(mcluts.TILING10_2_), _to_array(mcluts.TILING11), 
                _to_array(mcluts.TILING12_1_1), _to_array(mcluts.TILING12_1_1_), _to_array(mcluts.TILING12_1_2), 
                _to_array(mcluts.TILING12_2), _to_array(mcluts.TILING12_2_), _to_array(mcluts.TILING13_1), 
                _to_array(mcluts.TILING13_1_), _to_array(mcluts.TILING13_2), _to_array(mcluts.TILING13_2_), 
                _to_array(mcluts.TILING13_3), _to_array(mcluts.TILING13_3_), _to_array(mcluts.TILING13_4), 
                _to_array(mcluts.TILING13_5_1), _to_array(mcluts.TILING13_5_2), _to_array(mcluts.TILING14),
                
                _to_array(mcluts.TEST3), _to_array(mcluts.TEST4), _to_array(mcluts.TEST6), 
                _to_array(mcluts.TEST7), _to_array(mcluts.TEST10), _to_array(mcluts.TEST12), 
                _to_array(mcluts.TEST13), _to_array(mcluts.SUBCONFIG13),
                )
    
    return mcluts.THE_LUTS

