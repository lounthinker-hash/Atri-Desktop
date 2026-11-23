/* Bilinear inverse deformation on a rectilinear grid. All coordinates refer
   to pixel boundaries. Interpolate premultiplied colour at transparent edges.
   Build on Windows x64 with gcc -O3 -shared -static-libgcc warp_rgba.c -o warp_rgba.dll */
#include <math.h>
#include <stdint.h>

static const uint8_t empty[4] = {0, 0, 0, 0};

static inline const uint8_t *pixel(const uint8_t *src, int w, int h, int x, int y) {
    return ((unsigned)x < (unsigned)w && (unsigned)y < (unsigned)h)
        ? src + 4 * (y * w + x) : empty;
}

static inline void sample(const uint8_t *src, int w, int h, double x, double y, uint8_t *dst) {
    if (x <= -1 || y <= -1 || x >= w || y >= h) {
        dst[0] = dst[1] = dst[2] = dst[3] = 0;
        return;
    }
    int ix = (int)floor(x), iy = (int)floor(y);
    int fx = (int)((x - ix) * 256 + .5), fy = (int)((y - iy) * 256 + .5);
    const uint8_t *p = pixel(src, w, h, ix, iy), *q = pixel(src, w, h, ix + 1, iy);
    const uint8_t *r = pixel(src, w, h, ix, iy + 1), *s = pixel(src, w, h, ix + 1, iy + 1);
    uint32_t a = (256-fx)*(256-fy), b = fx*(256-fy), c = (256-fx)*fy, d = fx*fy;
    if (!(p[3] | q[3] | r[3] | s[3])) {
        dst[0] = dst[1] = dst[2] = dst[3] = 0;
    } else if ((p[3] & q[3] & r[3] & s[3]) == 255) {
        for (int k = 0; k < 3; ++k)
            dst[k] = (uint8_t)((a*p[k] + b*q[k] + c*r[k] + d*s[k] + 32768) >> 16);
        dst[3] = 255;
    } else {
        a *= p[3]; b *= q[3]; c *= r[3]; d *= s[3];
        uint32_t alpha = a + b + c + d;
        for (int k = 0; k < 3; ++k)
            dst[k] = alpha ? (uint8_t)((a*p[k] + b*q[k] + c*r[k] + d*s[k] + alpha/2)/alpha) : 0;
        dst[3] = (uint8_t)((alpha + 32768) >> 16);
    }
}

__declspec(dllexport) void warp_rgba(const uint8_t *src, int w, int h, uint8_t *dst, int stride,
                                     const int32_t *xs, int nx, const int32_t *ys, int ny,
                                     const double *grid) {
    for (int row = 0; row < ny-1; ++row) {
        for (int col = 0; col < nx-1; ++col) {
            const double *a = grid + 2*(row*nx + col), *b = a+2;
            const double *c = a+2*nx, *d = c+2;
            double ih = 1. / (ys[row+1]-ys[row]), iw = 1. / (xs[col+1]-xs[col]);
            for (int y = ys[row]; y < ys[row+1]; ++y) {
                double t = (y+.5-ys[row])*ih;
                double lx = a[0]+t*(c[0]-a[0]), ly = a[1]+t*(c[1]-a[1]);
                double dx = (b[0]+t*(d[0]-b[0])-lx)*iw, dy = (b[1]+t*(d[1]-b[1])-ly)*iw;
                double sx = lx+.5*dx-.5, sy = ly+.5*dy-.5;
                uint8_t *out = dst+4*(y*stride+xs[col]);
                for (int x = xs[col]; x < xs[col+1]; ++x, out += 4, sx += dx, sy += dy)
                    sample(src,w,h,sx,sy,out);
            }
        }
    }
}
