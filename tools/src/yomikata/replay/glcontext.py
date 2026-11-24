"""A headless OpenGL context for cores that draw with the graphics card (libretro's "hardware render"): EGL
without a window, and a framebuffer the core draws into and the frame is read back from.

With Mesa's software driver (`LIBGL_ALWAYS_SOFTWARE=1`, `EGL_PLATFORM=surfaceless`) nothing but the CPU is
used, which is slow and enough: a frame is wanted to look at, not to play."""

from __future__ import annotations

import ctypes as C

EGL_NONE, EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT = 0x3038, 0x3033, 1, 0x3040, 8
EGL_OPENGL_API, EGL_PLATFORM_SURFACELESS, EGL_EXTENSIONS = 0x30A2, 0x31DD, 0x3055
EGL_CONTEXT_MAJOR, EGL_CONTEXT_MINOR, EGL_CONTEXT_PROFILE, EGL_CORE_BIT, EGL_COMPAT_BIT = 0x3098, 0x30FB, 0x30FD, 1, 2
GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_DEPTH_STENCIL_ATTACHMENT, GL_FRAMEBUFFER_COMPLETE = 0x8D40, 0x8CE0, 0x821A, 0x8CD5
GL_TEXTURE_2D, GL_RGBA, GL_RGBA8, GL_UNSIGNED_BYTE, GL_RENDERBUFFER, GL_DEPTH24_STENCIL8 = 0x0DE1, 0x1908, 0x8058, 0x1401, 0x8D41, 0x88F0
GL_READ_FRAMEBUFFER, GL_PACK_ALIGNMENT, GL_VERSION, GL_RENDERER = 0x8CA8, 0x0D05, 0x1F02, 0x1F01
GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_NEAREST = 0x2801, 0x2800, 0x2600


class GLError(RuntimeError):
    pass


class Context:
    """An OpenGL context of the version asked for, current on the calling thread, with a framebuffer of its own."""

    def __init__(self, major: int = 3, minor: int = 3, core_profile: bool = True):
        try:
            egl = self.egl = C.CDLL("libEGL.so.1")
        except OSError as e:
            raise GLError(f"no EGL library: {e}") from e
        egl.eglGetPlatformDisplay.restype = C.c_void_p
        egl.eglGetPlatformDisplay.argtypes = [C.c_uint, C.c_void_p, C.c_void_p]
        egl.eglGetDisplay.restype = C.c_void_p
        egl.eglGetDisplay.argtypes = [C.c_void_p]
        egl.eglInitialize.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int)]
        egl.eglChooseConfig.argtypes = [C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_void_p), C.c_int, C.POINTER(C.c_int)]
        egl.eglCreateContext.restype = C.c_void_p
        egl.eglCreateContext.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]
        egl.eglMakeCurrent.argtypes = [C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p]
        egl.eglGetProcAddress.restype = C.c_void_p
        egl.eglGetProcAddress.argtypes = [C.c_char_p]
        egl.eglDestroyContext.argtypes = [C.c_void_p, C.c_void_p]
        egl.eglTerminate.argtypes = [C.c_void_p]

        self.display = egl.eglGetPlatformDisplay(EGL_PLATFORM_SURFACELESS, None, None) or egl.eglGetDisplay(None)
        if not self.display or not egl.eglInitialize(self.display, None, None):
            raise GLError("EGL has no display to offer (is a driver installed, and EGL_PLATFORM=surfaceless set?)")
        if not egl.eglBindAPI(EGL_OPENGL_API):
            raise GLError("EGL has no desktop OpenGL")
        config, count = C.c_void_p(), C.c_int()
        wanted = (C.c_int * 5)(EGL_SURFACE_TYPE, EGL_PBUFFER_BIT, EGL_RENDERABLE_TYPE, EGL_OPENGL_BIT, EGL_NONE)
        if not egl.eglChooseConfig(self.display, wanted, C.byref(config), 1, C.byref(count)) or not count.value:
            raise GLError("EGL has no OpenGL configuration")
        attributes = (C.c_int * 7)(EGL_CONTEXT_MAJOR, major, EGL_CONTEXT_MINOR, minor, EGL_CONTEXT_PROFILE,
                                   EGL_CORE_BIT if core_profile else EGL_COMPAT_BIT, EGL_NONE)
        self.context = egl.eglCreateContext(self.display, config, None, attributes)
        if not self.context:
            raise GLError(f"no OpenGL {major}.{minor} context to be had")
        if not egl.eglMakeCurrent(self.display, None, None, self.context):
            raise GLError("the context can't be made current without a surface")
        self.framebuffer, self.size = 0, (0, 0)
        get_string = self.function("glGetString", C.c_char_p, C.c_uint)
        self.version = (get_string(GL_VERSION) or b"").decode()
        self.renderer = (get_string(GL_RENDERER) or b"").decode()

    def address(self, name: bytes) -> int:
        return self.egl.eglGetProcAddress(name) or 0

    def function(self, name: str, restype, *argtypes):
        address = self.address(name.encode())
        if not address:
            raise GLError(f"the driver has no {name}")
        return C.CFUNCTYPE(restype, *argtypes)(address)

    def make_framebuffer(self, width: int, height: int) -> int:
        """A framebuffer of this size with colour, depth and stencil: what the core is told to draw into."""
        f, u, i, v = self.function, C.c_uint, C.c_int, C.c_void_p
        ids = (u * 1)()
        f("glGenFramebuffers", None, i, C.POINTER(u))(1, ids)
        self.framebuffer = ids[0]
        f("glBindFramebuffer", None, u, u)(GL_FRAMEBUFFER, self.framebuffer)
        f("glGenTextures", None, i, C.POINTER(u))(1, ids)
        f("glBindTexture", None, u, u)(GL_TEXTURE_2D, ids[0])
        f("glTexImage2D", None, u, i, i, i, i, i, u, u, v)(GL_TEXTURE_2D, 0, GL_RGBA8, width, height, 0, GL_RGBA,
                                                           GL_UNSIGNED_BYTE, None)
        for name in (GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER):
            f("glTexParameteri", None, u, u, i)(GL_TEXTURE_2D, name, GL_NEAREST)
        f("glFramebufferTexture2D", None, u, u, u, u, i)(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, ids[0], 0)
        f("glGenRenderbuffers", None, i, C.POINTER(u))(1, ids)
        f("glBindRenderbuffer", None, u, u)(GL_RENDERBUFFER, ids[0])
        f("glRenderbufferStorage", None, u, u, i, i)(GL_RENDERBUFFER, GL_DEPTH24_STENCIL8, width, height)
        f("glFramebufferRenderbuffer", None, u, u, u, u)(GL_FRAMEBUFFER, GL_DEPTH_STENCIL_ATTACHMENT, GL_RENDERBUFFER, ids[0])
        if f("glCheckFramebufferStatus", u, u)(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
            raise GLError("the framebuffer isn't complete")
        self.size = (width, height)
        self._bind = f("glBindFramebuffer", None, u, u)
        self._read = f("glReadPixels", None, i, i, i, i, u, u, v)
        self._store = f("glPixelStorei", None, u, i)
        self._finish = f("glFinish", None)
        return self.framebuffer

    def read(self, width: int, height: int) -> bytes:
        """The framebuffer's lower left `width` by `height` as RGBA rows, the bottom row first."""
        width, height = min(width, self.size[0]), min(height, self.size[1])
        out = C.create_string_buffer(width * height * 4)
        self._finish()
        self._bind(GL_READ_FRAMEBUFFER, self.framebuffer)
        self._store(GL_PACK_ALIGNMENT, 1)
        self._read(0, 0, width, height, GL_RGBA, GL_UNSIGNED_BYTE, out)
        return out.raw

    def close(self) -> None:
        if self.context:
            self.egl.eglMakeCurrent(self.display, None, None, None)
            self.egl.eglDestroyContext(self.display, self.context)
            self.egl.eglTerminate(self.display)
            self.context = None
