using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Automation;
using Windows.Graphics.Imaging;
using Windows.Media.Ocr;

// The parent receives labels, physical bounds, and session-keyed line aliases.
// Recognized text and captured pixels remain in this process.
static class ScreenContextWorker
{
    [StructLayout(LayoutKind.Sequential)] struct Rect { public int Left, Top, Right, Bottom; }
    [StructLayout(LayoutKind.Sequential)] struct LastInput { public uint Size, Time; }
    [StructLayout(LayoutKind.Sequential,CharSet=CharSet.Unicode)]
    struct MonitorInfo { public int Size; public Rect Bounds,Work;public uint Flags;
        [MarshalAs(UnmanagedType.ByValTStr,SizeConst=32)] public string Device; }
    [DllImport("user32")] static extern IntPtr MonitorFromRect(ref Rect rect,uint flags);
    [DllImport("user32",CharSet=CharSet.Unicode)] static extern bool GetMonitorInfo(IntPtr monitor,ref MonitorInfo info);
    [DllImport("user32")] static extern IntPtr GetForegroundWindow();
    [DllImport("user32")] static extern bool PrintWindow(IntPtr window, IntPtr dc, uint flags);
    [DllImport("user32")] static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32")] static extern bool IsIconic(IntPtr window);
    [DllImport("user32")] static extern bool IsHungAppWindow(IntPtr window);
    [DllImport("user32")] static extern bool GetWindowDisplayAffinity(IntPtr window, out uint affinity);
    [DllImport("user32")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr window, StringBuilder text, int capacity);
    [DllImport("user32")] static extern bool GetLastInputInfo(ref LastInput input);
    [DllImport("user32")] static extern IntPtr OpenInputDesktop(uint flags, bool inherit, uint access);
    [DllImport("user32", CharSet=CharSet.Unicode)] static extern bool GetUserObjectInformation(IntPtr handle, int index, StringBuilder text, uint size, out uint required);
    [DllImport("user32")] static extern bool CloseDesktop(IntPtr desktop);
    [DllImport("user32")] static extern bool SetProcessDpiAwarenessContext(IntPtr value);
    static readonly Regex PrivateText = Rule(@"password|passcode|passkey|sign[ -]?in|log[ -]?in|authentica|one.time (code|password)|credit card|payment|checkout|wallet|account security|private browsing|inprivate|incognito|密[码碼]|口令|验证码|驗證碼|登录|登入|支付|付款|收银台|银行卡|银行账户|網上銀行|网上银行|无痕|隐私浏览|無痕");
    static readonly Regex PrivateProcess = Rule(@"keepass|1password|bitwarden|lastpass|dashlane|enpass|protonpass|nordpass|credential|consent|lockapp|logonui|winlogon|securui|authenticator|mstsc|msrdc|teamviewer|anydesk|rustdesk");
    static Regex Rule(string s) { return new Regex(s,RegexOptions.IgnoreCase|RegexOptions.CultureInvariant); }
    static readonly string[] Kinds={"math","coding","writing","video","music","game","study","system"};
    static readonly Regex[] Words={
        Rule(@"微积分|线性代数|矩阵|方程|导数|概率论|线性空间|特征值|数学分析|线性方程|calculus|algebra|matrix|eigenvalue|derivative|quadratic|equation|theorem|integral"),
        Rule(@"def\s+\w+|public static|#include|console\.log|function\s+\w+|import numpy|traceback|debugger|git commit|编译|调试|断点|源代码|编程|编译器|终端"),
        Rule(@"word count|paragraph|manuscript|outline|draft|revision|正文|段落|字数|草稿|提纲|修订|排版|写作|文稿"),
        Rule(@"播放速度|弹幕|倍速|视频播放|字幕|清晰度|bilibili|youtube|subtitles|playback speed|video player"),
        Rule(@"播放列表|歌单|歌词|专辑|音乐播放器|playlist|lyrics|album|spotify|apple music"),
        Rule(@"游戏设置|开始游戏|读取存档|存档|成就解锁|continue game|new game|game settings|load game|inventory|quest log"),
        Rule(@"章节|习题|课程|课本|课后|练习题|复习|教材|作业|学习|lesson|textbook|exercise|lecture|chapter|flashcards|coursework"),
        Rule(@"设备管理器|任务管理器|控制面板|系统设置|文件资源管理器|task manager|control panel|system settings|file explorer")
    };
    static string AppKind(string app) {
        if (Regex.IsMatch(app,@"^(code|devenv|pycharm64|idea64|rider64|webstorm64|clion64|androidstudio64|rstudio|windowsterminal|powershell_ise)$")) return "coding";
        if (Regex.IsMatch(app,@"^(winword|onenote|wps|notepad|notepad\+\+|typora|obsidian|libreoffice|soffice.bin)$")) return "writing";
        if (Regex.IsMatch(app,@"^(spotify|cloudmusic|qqmusic|music.ui|foobar2000|aimp)$")) return "music";
        if (Regex.IsMatch(app,@"^(vlc|potplayermini64|mpc-hc64|mpv|videos.ui|bilibili)$")) return "video";
        if (Regex.IsMatch(app,@"^(anki|zotero)$")) return "study";
        if (Regex.IsMatch(app,@"^(steam|atri-mydearmoments-|minecraft|genshinimpact|starrail)$")) return "game";
        if (Regex.IsMatch(app,@"^(explorer|systemsettings|taskmgr|mmc|control)$")) return "system";
        return null;
    }
    static string Result(string kind,string score,string reason,string source) {
        return String.Join("|",new[]{kind,score,reason,source});
    }
    static string None(string reason){return Result("unknown","0",reason,"none");}
    static string Normalize(string s){return Regex.Replace(s??"", @"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "");}
    // Require terminal-style diagnostics in the captured pixels. Window titles and
    // isolated words such as "error" never produce a build-result observation.
    // WinRT may split an English identifier into individual glyphs when a CJK
    // recognizer is active. Accept these narrowly inside a diagnostic signature.
    const string CompilerError=@"\b(?:fatal\s+)?e[ \t]*r[ \t]*r[ \t]*o[ \t]*r[ \t]+(?:CS|C|TS|LNK)\d{3,5}\b";
    static readonly Regex FailedBuild = Rule(
        @"\bbui[l1i]d failed\b|\bcompilation (?:failed|terminated)\b|编译失败|构建失败|" +
        CompilerError+"|" +
        @"\btraceback\s*\(most recent call last\)|\bsyntaxerror\s*:\s*invalid syntax|" +
        @"\b(?:\d+\s+failed(?:,\s*\d+\s+(?:passed|skipped))*\s+in\s+\d|FAILED\s+\S+::\S+)");
    static readonly Regex PassedBuild = Rule(
        @"\bbui[l1i]d succeeded\b|\bcompiled successfully\b|编译成功|构建成功|全部测试通过|" +
        @"\b[1-9]\d*\s+passed(?:,\s*\d+\s+(?:skipped|warnings?))*\s+in\s+[\d.]+\s*s\b|" +
        @"\btests? passed\s*[:：]\s*[1-9]\d*\b");
    static readonly Regex ErrorDetail=Rule(CompilerError+@"|\bFAILED\s+\S+::\S+|\b(?:[A-Za-z]*Error|[A-Za-z]*Exception)\s*[:：]\s*\S");
    static readonly Regex TestCount=Rule(@"\b\d+\s+(?:passed|failed)\b|\btests? passed\s*[:：]\s*\d+");
    static readonly Regex Instruction=Rule(@"(?:求(?:出|解|证|證|导|極限|极限)?|计算|計算|证明|證明|解出|化简|化簡|判断|判斷|写出|解释|说明|推导|比较|填写|试问|請問|请问|为什么|為什麼)|\b(?:solve|compute|calculate|evaluate|simplify|differentiate|prove|determine|find|explain|compare|derive|identify|describe|justify|what|why|how)\b");
    static readonly Regex Formula=Rule(@"(?:[\p{L}\d)\]]\s*[=＝<>≤≥≈]\s*[-+\p{L}\d(\[]|[a-z\d)]\s*[+*/^×÷−]\s*[a-z\d(]|[∫∑√])");
    static readonly Regex CodeDetail=Rule(@"\b(?:def|class|function|import)\s+\w+|\b(?:return|throw|raise)\s+\w+|#\s*include\s*[<""]|\b\w+\.\w+\s*\(");
    static readonly Regex GenericChrome=Rule(@"^(?:problems?|output|terminal|editor|debug console|extensions|errors?|章节|课后习题|练习题|习题|作业|课程|教材|章节目录|课程目录|文档目录|lesson|chapter|exercises?|coursework)\s*[:：\d.\-]*$");
    // A new random secret arrives through the parent process's anonymous input pipe.
    // It allows equality checks between consecutive samples without exporting text.
    static HMACSHA256 sampleKey;
    static string Classify(string app,string title,string text,bool recognized){
        if(recognized && FailedBuild.IsMatch(text))return Result("coding_error","0.96","build_diagnostic","ocr");
        if(recognized && PassedBuild.IsMatch(text))return Result("coding_success","0.96","build_diagnostic","ocr");
        string appKind=AppKind(app), best=null; int score=0; bool evidence=false, fromTitle=false;
        for(int i=0;i<Kinds.Length;i++){
            int ct=Words[i].Matches(title).Cast<Match>().Select(m=>m.Value.ToLowerInvariant()).Distinct().Count();
            int co=Words[i].Matches(text).Cast<Match>().Select(m=>m.Value.ToLowerInvariant()).Distinct().Count();
            int rank=(Kinds[i]==appKind?4:0)+Math.Min(3,ct)*3+Math.Min(4,co)*2;
            if(Kinds[i]=="math" && recognized && text.Split('\n').Any(s=>Formula.IsMatch(s) && Instruction.IsMatch(s))){
                rank+=5;co++;
            }
            if(rank>score){score=rank;best=Kinds[i];evidence=co>0;fromTitle=ct>0;}
        }
        if(best==null)return Result("unknown","0","no_match",recognized?"ocr":"window");
        string confidence=score>=7?"0.93":score>=4?"0.82":score>=3?"0.71":"0.58";
        return Result(best,confidence,evidence?"content_match":fromTitle?"title_match":"app_match",evidence?"ocr":fromTitle?"title":"process");
    }
    static bool DefaultDesktop(){
        IntPtr d=OpenInputDesktop(0,false,1);if(d==IntPtr.Zero)return false;
        try{uint n;var name=new StringBuilder(128);return GetUserObjectInformation(d,2,name,256,out n)&&name.ToString().Equals("Default",StringComparison.OrdinalIgnoreCase);}
        finally{CloseDesktop(d);}
    }
    static bool PasswordFocused(IntPtr window, string app){
        // Read the accessibility password flag only, never text box contents.
        var element=AutomationElement.FocusedElement;
        if(element!=null&&element.Current.IsPassword)return true;
        if(!Regex.IsMatch(app,@"^(chrome|msedge|firefox|brave|opera|iexplore)$"))return false;
        var root=AutomationElement.FromHandle(window);
        var visiblePassword=new AndCondition(
            new PropertyCondition(AutomationElement.IsPasswordProperty,true),
            new PropertyCondition(AutomationElement.IsOffscreenProperty,false));
        return root!=null&&root.FindFirst(TreeScope.Descendants,visiblePassword)!=null;
    }
    sealed class Reading {
        public string Text="";
        public OcrResult Recognized;
        public bool[] Highlighted;
        public double ScaleX,ScaleY;
        public int Width,Height;
    }
    static int ChannelDistance(Color a,Color b){
        return Math.Max(Math.Abs(a.R-b.R),Math.Max(Math.Abs(a.G-b.G),Math.Abs(a.B-b.B)));
    }
    static bool HighlightedLine(Bitmap image,OcrLine line){
        if(line.Words.Count==0)return false;
        var boxes=line.Words.Select(w=>w.BoundingRect);
        double left=boxes.Min(b=>b.X),right=boxes.Max(b=>b.X+b.Width);
        double top=boxes.Min(b=>b.Y),bottom=boxes.Max(b=>b.Y+b.Height),height=bottom-top;
        int above=(int)(top-.75*height),below=(int)(bottom+.75*height);
        if(right-left<12||height<8||above<0||below>=image.Height)return false;
        // A selection band changes the empty background throughout the ink box
        // relative to both outer edges. Colored text alone covers too few samples.
        var colors=new Dictionary<int,int>();
        const int samples=15;
        for(int col=0;col<samples;col++){
            int x=Math.Min(image.Width-1,Math.Max(0,(int)(left+(col+.5)*(right-left)/samples)));
            var upper=image.GetPixel(x,above);var lower=image.GetPixel(x,below);
            if(ChannelDistance(upper,lower)>24)continue;
            for(int row=0;row<3;row++){
                int y=Math.Min(image.Height-1,Math.Max(0,(int)(top+height*(.14+.35*row))));
                var fill=image.GetPixel(x,y);
                if(ChannelDistance(upper,fill)<26 || ChannelDistance(lower,fill)<26)continue;
                int key=(fill.R>>4)*256+(fill.G>>4)*16+(fill.B>>4),count;
                colors.TryGetValue(key,out count);colors[key]=count+1;
            }
        }
        return colors.Values.Any(count=>count>=25);
    }
    static async Task<Reading> ReadImage(Bitmap image) {
        var reading=new Reading();
        var engine=OcrEngine.TryCreateFromUserProfileLanguages();
        if(engine==null){var lang=OcrEngine.AvailableRecognizerLanguages.FirstOrDefault();if(lang!=null)engine=OcrEngine.TryCreateFromLanguage(lang);}
        if(engine==null)return reading;
        double scale=Math.Min(1.0,Math.Min(1650.0/Math.Max(1,image.Width),1100.0/Math.Max(1,image.Height)));
        using(var small=new Bitmap(Math.Max(1,(int)(image.Width*scale)),Math.Max(1,(int)(image.Height*scale))))
        using(var memory=new MemoryStream()){
            using(var g=Graphics.FromImage(small)){g.InterpolationMode=InterpolationMode.HighQualityBilinear;g.DrawImage(image,0,0,small.Width,small.Height);}
            small.Save(memory,ImageFormat.Png);memory.Position=0;
            using(var stream=memory.AsRandomAccessStream()){
                var decoder=await BitmapDecoder.CreateAsync(stream);
                using(var bitmap=await decoder.GetSoftwareBitmapAsync(BitmapPixelFormat.Bgra8,BitmapAlphaMode.Ignore)){
                    var result=await engine.RecognizeAsync(bitmap);
                    reading.Text=Normalize(String.Join("\n",result.Lines.Select(l=>l.Text)));
                    reading.Recognized=result;
                    if(Math.Abs(result.TextAngle.GetValueOrDefault())<5)
                        reading.Highlighted=result.Lines.Select(line=>HighlightedLine(small,line)).ToArray();
                    reading.ScaleX=(double)image.Width/small.Width;
                    reading.ScaleY=(double)image.Height/small.Height;
                    reading.Width=small.Width;reading.Height=small.Height;
                    return reading;
                }
            }
        }
    }
    sealed class Candidate {
        public Rect Bounds, Monitor;
        public int Tier, LocalX, LocalY;
        public bool Highlighted;
        public string Device, Tag;
    }
    static int EvidenceTier(string kind,string text,Regex category){
        if(GenericChrome.IsMatch(text))return 0;
        if(kind=="coding_error")
            return ErrorDetail.IsMatch(text)?4:FailedBuild.IsMatch(text)?2:0;
        if(kind=="coding_success")
            return PassedBuild.IsMatch(text)?(TestCount.IsMatch(text)?4:3):0;
        if(kind=="math" || kind=="study"){
            bool formula=Formula.IsMatch(text), keyword=category.IsMatch(text);
            var task=Instruction.Match(text);
            bool hasQuestion=task.Success && text.Length-task.Length>=4;
            if(hasQuestion && (keyword||formula))return 4;
            if(formula && kind=="math")return 3;
            if(hasQuestion)return 3;
            return keyword?1:0;
        }
        if(kind=="coding" && CodeDetail.IsMatch(text))return 4;
        return category.IsMatch(text)?1:0;
    }
    static IEnumerable<OcrWord> FocusWords(OcrLine line,string kind,Regex category){
        // For a line spanning the viewport, keep the actual equation, instruction,
        // or diagnostic glyphs as the point of attention. The character can then
        // stand alongside the passage even if neither end of the full line is free.
        var preferred=kind=="coding_error"?new[]{ErrorDetail,FailedBuild}:
            kind=="coding_success"?new[]{TestCount,PassedBuild}:
            kind=="math"||kind=="study"?new[]{Formula,Instruction,category}:
            kind=="coding"?new[]{CodeDetail,category}:new[]{category};
        var words=line.Words;
        foreach(var rule in preferred){
            for(int length=1;length<=Math.Min(7,words.Count);length++){
                for(int i=0;i<=words.Count-length;i++){
                    var span=words.Skip(i).Take(length).ToArray();
                    if(rule.IsMatch(Normalize(String.Join(" ",span.Select(w=>w.Text)))))
                        return span;
                }
            }
        }
        return words;
    }
    static Rect PhysicalBounds(IEnumerable<OcrWord> words,Reading reading,Rect windowRect,double cs,double sn){
        double ox=reading.Width/2.0,oy=reading.Height/2.0;
        double left=double.MaxValue,top=double.MaxValue,right=double.MinValue,bottom=double.MinValue;
        foreach(var word in words){
            var b=word.BoundingRect;
            foreach(double x in new double[]{b.X,b.X+b.Width}){
                foreach(double y in new double[]{b.Y,b.Y+b.Height}){
                    double rx=ox+(x-ox)*cs-(y-oy)*sn,ry=oy+(x-ox)*sn+(y-oy)*cs;
                    left=Math.Min(left,rx);right=Math.Max(right,rx);
                    top=Math.Min(top,ry);bottom=Math.Max(bottom,ry);
                }
            }
        }
        return new Rect{Left=windowRect.Left+(int)Math.Floor(left*reading.ScaleX),
            Top=windowRect.Top+(int)Math.Floor(top*reading.ScaleY),
            Right=windowRect.Left+(int)Math.Ceiling(right*reading.ScaleX),
            Bottom=windowRect.Top+(int)Math.Ceiling(bottom*reading.ScaleY)};
    }
    static string Box(Rect r){return String.Join(",",new[]{r.Left,r.Top,r.Right-r.Left,r.Bottom-r.Top});}
    static string WithTarget(string decision,Reading reading,Rect windowRect,IntPtr window){
        return TargetDecision(decision,reading,windowRect,window,false);
    }
    static string TargetDecision(string decision,Reading reading,Rect windowRect,IntPtr window,bool fixture){
        var fields=decision.Split('|');
        if(fields[3]!="ocr"||reading.Recognized==null)return decision;
        var kind=fields[0];int index=Array.IndexOf(Kinds,kind);
        var regex=kind=="coding_error"?FailedBuild:kind=="coding_success"?PassedBuild:index>=0?Words[index]:null;
        if(regex==null)return decision;
        double angle=reading.Recognized.TextAngle.GetValueOrDefault()*Math.PI/180.0;
        double cs=Math.Cos(angle),sn=Math.Sin(angle);
        var candidates=new List<Candidate>();
        var lines=reading.Recognized.Lines;
        for(int n=0;n<lines.Count;n++){
            var line=lines[n];
            if(line.Words.Count==0)continue;
            string text=Normalize(line.Text).Trim();
            int tier=EvidenceTier(kind,text,regex);
            if(tier==0)continue;
            var r=PhysicalBounds(line.Words,reading,windowRect,cs,sn);
            double maxWidth=Math.Max(280,Math.Min(600,(windowRect.Right-windowRect.Left)*.60));
            if(r.Right-r.Left>maxWidth)r=PhysicalBounds(FocusWords(line,kind,regex),reading,windowRect,cs,sn);
            var m=windowRect;
            string device=@"\\.\DISPLAY1";
            if(!fixture){
                int cx=r.Left+(r.Right-r.Left)/2,cy=r.Top+(r.Bottom-r.Top)/2;
                var center=new Rect{Left=cx,Top=cy,Right=cx+1,Bottom=cy+1};
                var info=new MonitorInfo{Size=Marshal.SizeOf(typeof(MonitorInfo))};
                var monitor=MonitorFromRect(ref center,0);
                if(monitor==IntPtr.Zero||!GetMonitorInfo(monitor,ref info))continue;
                m=info.Bounds;device=info.Device;
            }
            var point=new Candidate{LocalX=r.Left-windowRect.Left,LocalY=r.Top-windowRect.Top,
                Highlighted=reading.Highlighted!=null && reading.Highlighted[n],
                Tier=tier,Device=device,Monitor=m};
            r.Left=Math.Max(r.Left,m.Left);r.Top=Math.Max(r.Top,m.Top);
            r.Right=Math.Min(r.Right,m.Right);r.Bottom=Math.Min(r.Bottom,m.Bottom);
            if(r.Right<=r.Left||r.Bottom<=r.Top)continue;
            point.Bounds=r;
            if(sampleKey!=null){
                string normalized=Regex.Replace(text.ToLowerInvariant(),@"\s+","");
                var signature=sampleKey.ComputeHash(Encoding.UTF8.GetBytes(normalized));
                point.Tag=BitConverter.ToString(signature,0,8).Replace("-","");
            }
            candidates.Add(point);
        }
        var ranked=candidates.OrderByDescending(c=>c.Tier).ThenByDescending(c=>c.Highlighted).Take(8).ToArray();
        if(ranked.Length==0)return decision;
        var first=ranked[0];
        var output=decision+"|"+Box(first.Bounds)+"|"+Box(first.Monitor)+"|"+first.Device+"|"+window.ToInt64();
        if(sampleKey!=null)output+="|v2|"+String.Join(";",ranked.Select(c=>String.Join("/",
            new[]{Box(c.Bounds),Box(c.Monitor),c.Device,c.Tier.ToString(),c.Tag,c.LocalX+","+c.LocalY+","+(c.Highlighted?"1":"0")})));
        return output;
    }
    static string Fixture(string path){
        using(var image=new Bitmap(path)){
            var read=ReadImage(image).GetAwaiter().GetResult();
            if(PrivateText.IsMatch(read.Text))return None("privacy");
            var rect=new Rect{Right=image.Width,Bottom=image.Height};
            return TargetDecision(Classify("","",read.Text,read.Text.Length>0),read,rect,new IntPtr(1),true);
        }
    }
    static string Observe(int owner,long expected) {
        if(!DefaultDesktop())return None("privacy");
        IntPtr window=GetForegroundWindow();
        if(window==IntPtr.Zero||IsIconic(window)||IsHungAppWindow(window))return None("unavailable");
        if(expected!=0 && window.ToInt64()!=expected)return None("changed");
        uint pid,aff;GetWindowThreadProcessId(window,out pid);
        if(pid==owner)return None("own_window");
        string app;
        try{using(var process=Process.GetProcessById((int)pid))app=process.ProcessName.ToLowerInvariant();}
        catch{return None("unavailable");}
        var name=new StringBuilder(768);GetWindowText(window,name,name.Capacity);string title=Normalize(name.ToString());
        if(PrivateProcess.IsMatch(app)||PrivateText.IsMatch(title)||
           (GetWindowDisplayAffinity(window,out aff)&&aff!=0))return None("privacy");
        if(title=="桌面亚托莉")return None("own_window");
        var input=new LastInput{Size=8};if(GetLastInputInfo(ref input)&&unchecked((uint)Environment.TickCount-input.Time)>300000)return Result("idle","1","inactive","system");
        try{if(PasswordFocused(window,app))return None("privacy");}catch{return None("privacy");}
        Rect rect;if(!GetWindowRect(window,out rect))return None("unavailable");
        int w=rect.Right-rect.Left,h=rect.Bottom-rect.Top;
        if(w<35||h<35||w>10000||h>10000||(long)w*h>25000000)return Classify(app,title,"",false);
        name.Clear();GetWindowText(window,name,name.Capacity);
        if(Normalize(name.ToString())!=title || GetForegroundWindow()!=window||!DefaultDesktop())return None("changed");
        using(var image=new Bitmap(w,h,PixelFormat.Format32bppRgb)){
            bool captured;
            using(var g=Graphics.FromImage(image)){IntPtr dc=g.GetHdc();try{captured=PrintWindow(window,dc,2);}finally{g.ReleaseHdc(dc);}}
            if(GetForegroundWindow()!=window||!DefaultDesktop())return None("changed");
            if(!captured)return Classify(app,title,"",false);
            Reading reading;
            try{reading=ReadImage(image).GetAwaiter().GetResult();}catch{return Classify(app,title,"",false);}
            if(PrivateText.IsMatch(reading.Text))return None("privacy");
            var result=WithTarget(Classify(app,title,reading.Text,reading.Text.Length>0),reading,rect,window);
            Rect current;
            if(GetForegroundWindow()!=window||!DefaultDesktop()||!GetWindowRect(window,out current)||
                rect.Left!=current.Left||rect.Top!=current.Top||rect.Right!=current.Right||rect.Bottom!=current.Bottom)
                return None("changed");
            return result;
        }
    }
    public static void Main(string[] args){
        try{
            if(args.Length>=3 && args[2]=="--targets-v2"){
                byte[] key=Convert.FromBase64String(Console.ReadLine()??"");
                if(key.Length!=32)throw new ArgumentException();
                sampleKey=new HMACSHA256(key);
            }
            SetProcessDpiAwarenessContext(new IntPtr(-4));
            Console.WriteLine(args.Length>=2 && args[0]=="--fixture-png" ? Fixture(args[1]) :
                Observe(Int32.Parse(args[0]),args.Length>1?Int64.Parse(args[1]):0));
        }
        catch{Console.WriteLine(None("unavailable"));}
        finally{if(sampleKey!=null)sampleKey.Dispose();}
    }
}

