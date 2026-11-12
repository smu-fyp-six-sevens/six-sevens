// Root - the hook's compositions. Open them with `npm run dev` (Remotion Studio).
//   Master          the hook with its on-screen headlines
//   Master-NoText   the same shots with no headlines, for laying your own text or voiceover over
//   Scene-*         one scene alone, for quick iteration (Scene-Buried is hook v3, Scene-Overwhelm is v2; Scene-Package and
//                   Scene-TooMany are hook v1, the portal version)

import { loadFont } from "@remotion/fonts";
import { Composition, staticFile } from "remotion";
import { Master } from "./Master";
import { BuriedScene } from "./scenes/BuriedScene";
import { OverwhelmScene } from "./scenes/OverwhelmScene";
import { PackageScene } from "./scenes/PackageScene";
import { PortalScene } from "./scenes/PortalScene";
import { PortalScene2 } from "./scenes/PortalScene2";
import { TamperScene } from "./scenes/TamperScene";
import { TooManyScene } from "./scenes/TooManyScene";
import { UploadScene } from "./scenes/UploadScene";
import { DetectScene } from "./scenes/DetectScene";
import { TraceScene } from "./scenes/TraceScene";
import { ScoreScene } from "./scenes/ScoreScene";
import { DashboardScene } from "./scenes/DashboardScene";
import { FinaliseScene } from "./scenes/FinaliseScene";
import { DifferenceScene } from "./scenes/DifferenceScene";
import { CtaScene } from "./scenes/CtaScene";
import { WaitingScene } from "./scenes/WaitingScene";
import { WaitingScene2 } from "./scenes/WaitingScene2";
import { canvas } from "./theme";
import { FPS, HOOK, OVERWHELM, OVERWHELM3, PORTAL, PORTAL2, TAMPER, UPLOAD, DETECT, TRACE, SCORE, DASHBOARD, DIFFERENCE, CTA, FINALISE, TOTAL_FRAMES, WAITING, WAITING2, atSeconds } from "./timing";

// Inter, bundled in public/fonts (OFL, see Inter-LICENSE.txt), so renders work offline.
for (const weight of ["400", "500", "600", "700"]) {
  loadFont({ family: "Inter", url: staticFile(`fonts/inter-latin-${weight}-normal.woff2`), weight });
}

const size = { fps: FPS, width: canvas.width, height: canvas.height };

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Master" component={Master} defaultProps={{ showText: true }} durationInFrames={TOTAL_FRAMES} {...size} />
    <Composition id="Master-NoText" component={Master} defaultProps={{ showText: false }} durationInFrames={TOTAL_FRAMES} {...size} />
    {/* Music and effects only: for laying your own voice recording over */}
    <Composition id="Master-NoVO" component={Master} defaultProps={{ showText: true, vo: false }} durationInFrames={TOTAL_FRAMES} {...size} />
    {/* Picture only */}
    <Composition id="Master-Silent" component={Master} defaultProps={{ showText: true, vo: false, music: false, sfx: false }} durationInFrames={TOTAL_FRAMES} {...size} />
    <Composition id="Scene-Finalise" component={FinaliseScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(FINALISE.length)} {...size} />
    <Composition id="Scene-Dashboard" component={DashboardScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(DASHBOARD.length)} {...size} />
    <Composition id="Scene-Difference" component={DifferenceScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(DIFFERENCE.length)} {...size} />
    <Composition id="Scene-Cta" component={CtaScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(CTA.length)} {...size} />
    <Composition id="Scene-Detect" component={DetectScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(DETECT.length)} {...size} />
    <Composition id="Scene-Trace" component={TraceScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(TRACE.length)} {...size} />
    <Composition id="Scene-Score" component={ScoreScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(SCORE.length)} {...size} />
    <Composition id="Scene-Upload" component={UploadScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(UPLOAD.length)} {...size} />
    <Composition id="Scene-Portal" component={PortalScene2} defaultProps={{ showText: true }} durationInFrames={atSeconds(PORTAL2.length)} {...size} />
    <Composition id="Scene-Portal-v1" component={PortalScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(PORTAL.length)} {...size} />
    <Composition id="Scene-Tamper" component={TamperScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(TAMPER.length)} {...size} />
    <Composition id="Scene-Waiting" component={WaitingScene2} defaultProps={{ showText: true }} durationInFrames={atSeconds(WAITING2.length)} {...size} />
    <Composition id="Scene-Waiting-v1" component={WaitingScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(WAITING.length)} {...size} />
    <Composition id="Scene-Buried" component={BuriedScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(OVERWHELM3.length)} {...size} />
    <Composition id="Scene-Overwhelm" component={OverwhelmScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(OVERWHELM.length)} {...size} />
    <Composition id="Scene-Package" component={PackageScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(HOOK.package.length)} {...size} />
    <Composition id="Scene-TooMany" component={TooManyScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(HOOK.tooMany.length)} {...size} />
  </>
);
