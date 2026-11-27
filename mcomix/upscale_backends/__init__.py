"""upscale_backends - Neural upscaling adapters.

Adapters convert an RGB PIL image into a larger RGB PIL image. They are
only used from the upscale worker thread and import their runtime lazily;
importing this package must stay cheap (no numpy, no runtime).

Adapters only run models on a GPU: they refuse to load a model that would
run on the CPU, in whole or in part, and never fall back to the CPU.
"""

class Cancelled(Exception):
    """ Raised by an adapter when asked to stop (between tiles). """


class ModelFailure(RuntimeError):
    """ Raised by an adapter when the model can no longer be used, e.g.
    because the GPU failed; other exceptions only concern one image. """

# vim: expandtab:sw=4:ts=4
