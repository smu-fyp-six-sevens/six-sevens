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
import { TooManyScene } from "./scenes/TooManyScene";
import { WaitingScene } from "./scenes/WaitingScene";
import { WaitingScene2 } from "./scenes/WaitingScene2";
import { canvas } from "./theme";
import { FPS, HOOK, OVERWHELM, OVERWHELM3, TOTAL_FRAMES, WAITING, WAITING2, atSeconds } from "./timing";

// Inter, bundled in public/fonts (OFL, see Inter-LICENSE.txt), so renders work offline.
for (const weight of ["400", "500", "600", "700"]) {
  loadFont({ family: "Inter", url: staticFile(`fonts/inter-latin-${weight}-normal.woff2`), weight });
}

const size = { fps: FPS, width: canvas.width, height: canvas.height };

export const RemotionRoot: React.FC = () => (
  <>
    <Composition id="Master" component={Master} defaultProps={{ showText: true }} durationInFrames={TOTAL_FRAMES} {...size} />
    <Composition id="Master-NoText" component={Master} defaultProps={{ showText: false }} durationInFrames={TOTAL_FRAMES} {...size} />
    <Composition id="Scene-Waiting" component={WaitingScene2} defaultProps={{ showText: true }} durationInFrames={atSeconds(WAITING2.length)} {...size} />
    <Composition id="Scene-Waiting-v1" component={WaitingScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(WAITING.length)} {...size} />
    <Composition id="Scene-Buried" component={BuriedScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(OVERWHELM3.length)} {...size} />
    <Composition id="Scene-Overwhelm" component={OverwhelmScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(OVERWHELM.length)} {...size} />
    <Composition id="Scene-Package" component={PackageScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(HOOK.package.length)} {...size} />
    <Composition id="Scene-TooMany" component={TooManyScene} defaultProps={{ showText: true }} durationInFrames={atSeconds(HOOK.tooMany.length)} {...size} />
  </>
);
