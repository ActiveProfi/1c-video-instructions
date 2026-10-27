using System;
using System.IO;
using System.Drawing;
using System.Drawing.Imaging;
using System.Collections.Generic;

// Minimal indexed Motion JPEG AVI. No external encoder or network dependency.
public sealed class DemoRecorder : IDisposable {
    readonly BinaryWriter w;
    readonly Rectangle screen;
    readonly int fps;
    readonly List<uint[]> index = new List<uint[]>();
    long riffSize, frameCount, streamLength, moviSize, moviBase;
    uint maxFrame;
    bool closed;
    void Four(string text) { w.Write(System.Text.Encoding.ASCII.GetBytes(text)); }
    void Patch(long position, uint value) { long end=w.BaseStream.Position; w.BaseStream.Position=position; w.Write(value); w.BaseStream.Position=end; }
    long List(string type) { Four("LIST"); long p=w.BaseStream.Position; w.Write(0u); Four(type); return p; }
    void EndList(long p) { Patch(p,(uint)(w.BaseStream.Position-p-4)); }
    public DemoRecorder(string path, int x, int y, int width, int height, int rate) {
        screen=new Rectangle(x,y,width,height); fps=rate;
        if(width<1 || height<1 || width>32767 || height>32767 || rate<1) throw new ArgumentException("Invalid capture dimensions");
        w=new BinaryWriter(File.Create(path));
        Four("RIFF"); riffSize=w.BaseStream.Position; w.Write(0u); Four("AVI ");
        long hdrl=List("hdrl"); Four("avih"); w.Write(56u);
        w.Write((uint)(1000000/rate)); w.Write(0u); w.Write(0u); w.Write(0x10u);
        frameCount=w.BaseStream.Position; w.Write(0u); w.Write(0u); w.Write(1u); w.Write(0u);
        w.Write((uint)width); w.Write((uint)height); for(int i=0;i<4;i++) w.Write(0u);
        long strl=List("strl"); Four("strh"); w.Write(56u);
        Four("vids"); Four("MJPG"); w.Write(0u); w.Write((ushort)0); w.Write((ushort)0);
        w.Write(0u); w.Write(1u); w.Write((uint)rate); w.Write(0u);
        streamLength=w.BaseStream.Position; w.Write(0u); w.Write(0u); w.Write(uint.MaxValue); w.Write(0u);
        w.Write((short)0); w.Write((short)0); w.Write((short)width); w.Write((short)height);
        Four("strf"); w.Write(40u); w.Write(40u); w.Write(width); w.Write(height);
        w.Write((ushort)1); w.Write((ushort)24); Four("MJPG"); w.Write((uint)(width*height*3));
        w.Write(0); w.Write(0); w.Write(0u); w.Write(0u); EndList(strl); EndList(hdrl);
        moviSize=List("movi"); moviBase=moviSize+4;
    }
    public void Frame(int x,int y,int width,int height,string caption) {
        using(var bitmap=new Bitmap(screen.Width,screen.Height,PixelFormat.Format24bppRgb)) {
            using(var g=Graphics.FromImage(bitmap)) {
                g.CopyFromScreen(screen.Location,Point.Empty,screen.Size);
                using(var pen=new Pen(Color.FromArgb(255,170,0),4))
                    g.DrawRectangle(pen,x-screen.X-4,y-screen.Y-4,width+8,height+8);
                int boxHeight=Math.Min(115,screen.Height/4);
                int boxY = (y-screen.Y+height+8 >= screen.Height-boxHeight) ? 0 : screen.Height-boxHeight;
                using(var brush=new SolidBrush(Color.FromArgb(235,22,30,45))) g.FillRectangle(brush,0,boxY,screen.Width,boxHeight);
                using(var font=new Font("Segoe UI",20))
                using(var brush=new SolidBrush(Color.White))
                    g.DrawString(caption,font,brush,new RectangleF(24,boxY+14,screen.Width-48,boxHeight-20));
            }
            WriteFrame(bitmap);
        }
    }
    public void WriteFrame(Bitmap bitmap) {
        if(closed) throw new ObjectDisposedException("DemoRecorder");
        if(bitmap.Width!=screen.Width || bitmap.Height!=screen.Height) throw new ArgumentException("Unexpected frame dimensions");
            using(var memory=new MemoryStream()) {
                bitmap.Save(memory,ImageFormat.Jpeg); byte[] data=memory.ToArray();
                uint offset=(uint)(w.BaseStream.Position-moviBase);
                Four("00dc"); w.Write((uint)data.Length); w.Write(data); if((data.Length&1)!=0) w.Write((byte)0);
                index.Add(new uint[]{offset,(uint)data.Length}); maxFrame=Math.Max(maxFrame,(uint)data.Length);
            }
    }
    public void Dispose() {
        if(closed) return; closed=true;
        try {
            EndList(moviSize); Four("idx1"); w.Write((uint)(index.Count*16));
            foreach(var entry in index) { Four("00dc"); w.Write(0x10u); w.Write(entry[0]); w.Write(entry[1]); }
            Patch(frameCount,(uint)index.Count); Patch(streamLength,(uint)index.Count);
            Patch(riffSize,(uint)(w.BaseStream.Length-8)); w.Flush();
        } finally { w.Dispose(); }
    }
}
