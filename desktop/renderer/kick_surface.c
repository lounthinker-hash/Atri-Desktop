#include <math.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>

static void sample(const uint8_t *ink,int w,int h,double x,double y,float v[4]){
    int ix=(int)floor(x),iy=(int)floor(y);
    if(ix<0||iy<0||ix+1>=w||iy+1>=h){memset(v,0,16);return;}
    float dx=x-ix,dy=y-iy;
    const uint8_t *p=ink+4*(iy*w+ix),*r=p+4*w;
    for(int j=0;j<4;j++) v[j]=(1-dy)*((1-dx)*p[j]+dx*p[4+j])+dy*((1-dx)*r[j]+dx*r[4+j]);
}
// Source textures contain premultiplied color. Vertices are corresponding
// source, destination and intermediate material positions in physical pixels.
__declspec(dllexport) void surface(uint8_t *out,int w,int h,const uint8_t *a,int aw,int ah,
  const uint8_t *b,int bw,int bh,const double *dst,const double *pa,const double *pb,
  const double *mix,int nr,int nc){
 for(int row=0;row<nr-1;row++)for(int col=0;col<nc-1;col++)for(int tri=0;tri<2;tri++){
    int k=row*nc+col;int ids[3]={k,tri?k+nc:k+1,k+nc+1};
    const double *v0=dst+ids[0]*2,*v1=dst+ids[1]*2,*v2=dst+ids[2]*2;
    double ax=v1[0]-v0[0],ay=v1[1]-v0[1],bx=v2[0]-v0[0],by=v2[1]-v0[1];
    double det=ax*by-ay*bx;if(fabs(det)<.00001)continue;
    int x0=fmax(0,floor(fmin(v0[0],fmin(v1[0],v2[0]))));int x1=fmin(w-1,ceil(fmax(v0[0],fmax(v1[0],v2[0]))));
    int y0=fmax(0,floor(fmin(v0[1],fmin(v1[1],v2[1]))));int y1=fmin(h-1,ceil(fmax(v0[1],fmax(v1[1],v2[1]))));
    for(int y=y0;y<=y1;y++)for(int x=x0;x<=x1;x++){
       double xx=x+.5-v0[0],yy=y+.5-v0[1];
       double u=(xx*by-yy*bx)/det,v=(ax*yy-ay*xx)/det,t=1-u-v;
       if(t<-.000002||u<-.000002||v<-.000002)continue;
       double fx=0,fy=0,gx=0,gy=0,m=0,z[3]={t,u,v};
       for(int i=0;i<3;i++){int p=ids[i];fx+=z[i]*pa[2*p];fy+=z[i]*pa[2*p+1];gx+=z[i]*pb[2*p];gy+=z[i]*pb[2*p+1];m+=z[i]*mix[p];}
       float l[4],r[4];
       if(m<.000001){sample(a,aw,ah,fx,fy,l);}
       else if(m>.999999){sample(b,bw,bh,gx,gy,l);}
       else{
          sample(a,aw,ah,fx,fy,l);sample(b,bw,bh,gx,gy,r);
          for(int j=0;j<4;j++)l[j]+=(r[j]-l[j])*m;
       }
       float alpha=l[3];if(alpha<.25)continue;
       uint8_t *q=out+4*(y*w+x);
       // Several triangles can meet at the outer bend of a cuff. Keep one
       // material surface at that pixel instead of stacking its antialiasing.
       if(alpha<q[3]-.51)continue;
       float factor=255.f/alpha;
       for(int j=0;j<3;j++)q[j]=(uint8_t)fminf(255.f,l[j]*factor+.5f);
       q[3]=(uint8_t)fminf(255.f,alpha+.5f);
    }
 }
}
