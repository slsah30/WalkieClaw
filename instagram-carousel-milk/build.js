// Generates index.html for the MILK RUN carousel — FINAL polish pass on the illustration-led build.
const fs = require('fs');
const OUT = __dirname + '/index.html';

const C = { dark:'#0B0F1A', bulb:'#FFD43B', milk:'#F6F1E7', duvet:'#1E2A4A', alarm:'#FF3B30', ghostl:'#9AA3BD', ghost:'#3A4666' };
// wedge polygon: hinge implied off-canvas; top width = W/2
const clip = w => `polygon(calc(100% - ${w}px) 100%, 100% 100%, 100% 0, calc(100% - ${w/2}px) 0)`;
// wedge = hard-edged near-flat Bulb slab + a soft blurred halo bleeding ~34px onto the dark side
const wedge = w => `<div class="glow"><div style="clip-path:${clip(w)}"></div></div><div class="wedge" style="clip-path:${clip(w)}"></div>`;
// everything the light touches flips to Duvet: duplicate the text layer, clipped by the same polygon
const lit = (w, inner) => `<div class="lit" style="clip-path:${clip(w)}">${inner}</div>`;
const rule = `<svg class="rule" viewBox="0 0 1080 1350"><path d="M72 200 H1008" stroke="${C.ghost}" stroke-width="2" stroke-dasharray="18 12"/></svg>`;
const header = t => `<div class="file">MILK RUN // FILE 0902</div><div class="clock">${t}</div>`;

// base route drawn on slide 3 (plan y 540..945): in from the hall door, up the boards, hop the towel, along, out the top toward the light
const BASE = 'M160 930 V800 Q160 772 200 768 A135 62 0 0 1 460 700 H860 V546';
const T4 = 'M600 700 V770';           // terminal: to the vault crack
const T5 = 'M460 700 L300 850';           // terminal: to the counter
const dot = (x,y,c=C.ghostl,r=3) => `<circle cx="${x}" cy="${y}" r="${r}" fill="${c}"/>`;
const leader = (d, x, y, c=C.ghostl, dash='') => `<path d="${d}" fill="none" stroke="${c}" stroke-width="2"${dash?` stroke-dasharray="${dash}"`:''}/>${dot(x,y,c)}`;

// text helpers (absolute HTML). 36px label, lh 1.25 => line box 45, baseline = top + 36
const label = (x, baseline, text, {color=C.ghostl, right=false, extra=''}={}) =>
  `<div class="label" style="${right?`right:${1080-x}px;text-align:right;`:`left:${x}px;`}top:${baseline-36}px;color:${color};${extra}">${text}</div>`;

function svgFull(cls, inner, defs='') {
  return `<svg class="full ${cls}" viewBox="0 0 1080 1350" xmlns="http://www.w3.org/2000/svg">${defs?`<defs>${defs}</defs>`:''}${inner}</svg>`;
}
function ghostRoute(n, segs=[], opacity=.45) {
  return svgFull('route', `<defs><clipPath id="band${n}"><rect x="0" y="470" width="1080" height="530"/></clipPath></defs>
  <g clip-path="url(#band${n})" fill="none" stroke="${C.ghost}" stroke-opacity="${opacity}" stroke-width="4" stroke-dasharray="14 10" stroke-linecap="round">
   <path d="${BASE}"/>${segs.map(s=>`<path d="${s}"/>`).join('')}</g>`);
}
// bendy straw: Bulb with Duvet under-stroke. pts = array of [x,y]
function straw(pts, {color=C.bulb, under=C.duvet, w=9}={}) {
  const d = 'M' + pts.map(p=>p.join(' ')).join(' L ');
  return `<path d="${d}" fill="none" stroke="${under}" stroke-width="${w+7}" stroke-linecap="round" stroke-linejoin="round"/>
          <path d="${d}" fill="none" stroke="${color}" stroke-width="${w}" stroke-linecap="round" stroke-linejoin="round"/>`;
}
// small carton: bottom-centre at (cx, by), width w, body height h, gable g. Milk body, Duvet panel with Milk level window.
function carton(cx, by, w, h, g, {stroke=C.duvet, level=.5}={}) {
  const x=cx-w/2, top=by-h-g;
  const pw=w*.68, ph=h*.55, px=cx-pw/2, py=by-h+ (h-ph)*.55;
  return `<g>
   <path d="M${x} ${by-h} L${cx} ${top} L${x+w} ${by-h} Z" fill="${C.milk}" stroke="${stroke}" stroke-width="4" stroke-linejoin="round"/>
   <rect x="${x}" y="${by-h}" width="${w}" height="${h}" rx="${w*.06}" fill="${C.milk}" stroke="${stroke}" stroke-width="4"/>
   <rect x="${cx-w*.12}" y="${top+g*.3}" width="${w*.24}" height="${g*.5}" fill="${C.milk}" stroke="${stroke}" stroke-width="3"/>
   <rect x="${px}" y="${py}" width="${pw}" height="${ph}" rx="6" fill="${C.duvet}"/>
   <rect x="${px+8}" y="${py+ph*(1-level)}" width="${pw-16}" height="${ph*level-8}" fill="${C.milk}"/>
  </g>`;
}

const slides = [];

/* ---------- SLIDE 1 : HOOK ---------- */
{
  const W=200;
  const head = `<div class="hook">HOW TO<br>DRINK<br>MILK</div><div class="sub">without waking<br>the guard.</div>`;
  slides.push(`
<div class="slide s1">
  ${wedge(W)}
  ${svgFull('illo', `
    <path d="M104 1130 H-10" fill="none" stroke="${C.ghostl}" stroke-width="2" stroke-dasharray="12 9"/>
    ${dot(104,1130)}
    <circle cx="140" cy="1130" r="36" fill="none" stroke="${C.alarm}" stroke-width="4"/>
  `)}
  ${header('02:06')}
  ${head}
  ${lit(W, head + `<div class="clock">02:06</div>`)}
  ${label(200,1142,'GUARD: DOWN. UNCONFIRMED.')}
  <div class="counter">01 / 09</div>
</div>`);
}

/* ---------- SLIDE 2 : THE WINDOW ---------- */
{
  const W=300;
  const head = `<div class="head"><span class="step">STEP 1.</span><span class="name">THE WINDOW.</span></div>`;
  const body = `<p class="body">Guard went down at 22:40.
You move at 02:07.
Not a minute before.</p>`;
  const illo = svgFull('illo', `
    <!-- couch, scaled up ~11% for thumbnail presence (x 72..680) -->
    <g transform="translate(72 520) scale(1.11) translate(-72 -520)">
      <rect x="92" y="548" width="516" height="130" rx="28" fill="${C.duvet}"/>
      <rect x="347" y="562" width="6" height="112" fill="${C.dark}"/>
      <rect x="92" y="668" width="516" height="88" rx="18" fill="${C.duvet}"/>
      <path d="M118 674 H582" stroke="${C.ghost}" stroke-width="3" opacity=".9"/>
      <rect x="347" y="678" width="6" height="72" fill="${C.dark}"/>
      <rect x="72" y="586" width="78" height="170" rx="36" fill="${C.duvet}"/>
      <rect x="550" y="586" width="70" height="170" rx="34" fill="${C.duvet}"/>
      <rect x="108" y="754" width="24" height="26" rx="4" fill="${C.duvet}"/>
      <rect x="566" y="754" width="24" height="26" rx="4" fill="${C.duvet}"/>
      <!-- body under blanket -->
      <path d="M196 668 C196 618 232 596 292 596 L500 600 C540 602 552 640 546 668 Z" fill="${C.milk}"/>
      <path d="M300 640 Q380 626 470 646" fill="none" stroke="${C.ghost}" stroke-width="3" opacity=".8"/>
      <path d="M236 668 Q250 650 262 668" fill="none" stroke="${C.ghost}" stroke-width="3" opacity=".8"/>
      <!-- head -->
      <circle cx="150" cy="590" r="40" fill="${C.duvet}" stroke="${C.dark}" stroke-width="6"/>
      <circle cx="150" cy="590" r="80" fill="none" stroke="${C.alarm}" stroke-width="3.6"/>
    </g>
    ${leader('M158 826 V702',158,700)}
    <!-- timeline -->
    <path d="M72 930 H1008" stroke="${C.milk}" stroke-width="3"/>
    <circle cx="200" cy="930" r="9" fill="${C.alarm}"/>
  `);
  const litSvg = svgFull('', `
    <path d="M72 930 H1008" stroke="${C.duvet}" stroke-width="3"/>
    <circle cx="960" cy="930" r="84" fill="url(#halo2)"/>
    <circle cx="960" cy="930" r="14" fill="${C.bulb}" stroke="${C.duvet}" stroke-width="6"/>
  `, `<radialGradient id="halo2"><stop offset="0" stop-color="${C.milk}" stop-opacity=".95"/><stop offset=".4" stop-color="${C.milk}" stop-opacity=".45"/><stop offset="1" stop-color="${C.milk}" stop-opacity="0"/></radialGradient>`);
  slides.push(`
<div class="slide">
  <div class="numeral">1</div>
  ${wedge(W)}
  ${illo}
  ${header('02:07')}
  ${rule}
  ${head}
  ${body}
  ${lit(W, litSvg + head + `<div class="clock">02:07</div>` + body + label(960,890,'GO',{extra:'transform:translateX(-50%);'}) + label(960,980,'02:07',{extra:'transform:translateX(-50%);'}))}
  ${label(72,860,'GUARD — COUCH. NOT BED. NOTED.')}
  ${label(200,980,'22:40',{extra:'transform:translateX(-50%);'})}
  <div class="counter">02 / 09</div>
</div>`);
}

/* ---------- SLIDE 3 : THE FLOOR ---------- */
{
  const W=400;
  const head = `<div class="head"><span class="step">STEP 2.</span><span class="name">THE FLOOR.</span></div>`;
  const body = `<p class="body">Third board creaks.
Someone laid a towel over it.
Don't ask.</p>`;
  const PT=540, PB=945, PH=PB-PT;
  const seams = {0:700,1:860,3:620,4:880,5:760,6:640,7:830};
  const boards = (fill, seam) => { let s='';
    for (let i=0;i<8;i++){ const x=72+i*112; s+=`<rect x="${x}" y="${PT}" width="96" height="${PH}" fill="${fill}"/>`;
      if(seams[i]!==undefined) s+=`<rect x="${x}" y="${seams[i]-3}" width="96" height="6" fill="${seam}"/>`;
      s+=`<rect x="${x+8}" y="${PT}" width="2" height="${PH}" fill="${C.ghost}" opacity=".35"/>`; }
    return s; };
  let stripes='';
  for (let k=-6;k<20;k++){ const x=170+k*24; stripes+=`<line x1="${x-150}" y1="775" x2="${x}" y2="625" stroke="${C.ghost}" stroke-width="2" opacity=".55"/>`; }
  const routeAttrs = c => `fill="none" stroke="${c}" stroke-width="4" stroke-dasharray="14 10" stroke-linecap="round"`;
  const illo = svgFull('illo', `
    ${boards(C.duvet, C.dark)}
    <rect x="341" y="${PT}" width="6" height="${PH}" fill="${C.alarm}"/>
    <!-- door symbol -->
    <path d="M110 ${PB-2} V${PB-92}" stroke="${C.milk}" stroke-width="5" stroke-linecap="round"/>
    <path d="M110 ${PB-92} A90 90 0 0 1 200 ${PB-2}" fill="none" stroke="${C.milk}" stroke-width="3" stroke-dasharray="6 6"/>
    <!-- towel -->
    <g transform="rotate(-4 320 700)">
      <rect x="170" y="625" width="300" height="150" rx="22" fill="${C.milk}"/>
      <g clip-path="url(#towelClip)">${stripes}
        <rect x="196" y="625" width="12" height="150" fill="${C.duvet}"/><rect x="432" y="625" width="12" height="150" fill="${C.duvet}"/></g>
    </g>
    <!-- route: Milk on the boards, Duvet where it hops the towel -->
    <path d="${BASE}" ${routeAttrs(C.milk)}/>
    <g clip-path="url(#towelClipR)"><path d="${BASE}" ${routeAttrs(C.duvet)}/></g>
    <circle cx="160" cy="930" r="6" fill="${C.milk}"/>
    ${leader('M240 528 L262 630',263,636)}
    ${leader('M600 944 V712',600,708)}
  `, `<clipPath id="towelClip"><rect x="170" y="625" width="300" height="150" rx="22"/></clipPath>
      <clipPath id="towelClipR"><rect x="170" y="625" width="300" height="150" rx="22" transform="rotate(-4 320 700)"/></clipPath>`);
  // inside the light: boards flip to Milk (gaps show the Bulb), the route flips to Duvet, arrowhead out the top
  const litSvg = svgFull('', `
    ${boards(C.milk, C.bulb)}
    <path d="${BASE}" ${routeAttrs(C.duvet)}/>
    <path d="M846 566 L860 546 L874 566" fill="none" stroke="${C.duvet}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
  `);
  slides.push(`
<div class="slide">
  <div class="numeral">2</div>
  ${wedge(W)}
  ${illo}
  ${header('02:09')}
  ${rule}
  ${head}
  ${body}
  ${lit(W, litSvg + head + `<div class="clock">02:09</div>` + body + label(72,988,'SOCKS. NOT SLIPPERS. SLIPPERS SLAP.'))}
  <div class="label vert" style="left:290px;top:770px;color:${C.alarm}">CREAK</div>
  ${label(72,516,'TOWEL — ORIGIN UNKNOWN.')}
  ${label(72,988,'SOCKS. NOT SLIPPERS. SLIPPERS SLAP.')}
  <div class="counter">03 / 09</div>
</div>`);
}

/* ---------- SLIDE 4 : THE VAULT ---------- */
{
  const head = `<div class="head"><span class="step">STEP 3.</span><span class="name">THE VAULT.</span></div>`;
  // dial ticks on an ellipse at (760,790) rx62 ry72
  let ticks='';
  for(let k=0;k<12;k++){ const a=k*Math.PI/6; const c=Math.cos(a), s=Math.sin(a);
    ticks+=`<line x1="${760+50*c}" y1="${790+60*s}" x2="${760+60*c}" y2="${790+70*s}" stroke="${C.milk}" stroke-width="${k%3?2:4}" stroke-linecap="round"/>`; }
  const T = 'translate(1000 1000) scale(.92) translate(-1000 -1000)'; // vault slightly smaller, dropped ~30px, anchored bottom-right
  const illo = svgFull('illo', `
    <!-- halo: the crack and the spill glow softly onto the dark -->
    <g filter="url(#blur4)" opacity=".55">
      <polygon points="600,700 600,990 160,1000 160,840" fill="${C.bulb}"/>
      <rect x="570" y="650" width="70" height="340" fill="${C.bulb}"/>
    </g>
    <!-- floor light spill: flat Bulb core, falloff only in the outer 120px -->
    <polygon points="600,700 600,990 160,1000 160,840" fill="url(#spill4)"/>
    <g transform="${T}">
      <!-- vault body -->
      <rect x="520" y="600" width="480" height="380" rx="26" fill="${C.duvet}"/>
      <rect x="538" y="618" width="444" height="344" rx="16" fill="none" stroke="${C.ghost}" stroke-width="2" opacity=".8"/>
      <!-- lit interior seen through the crack: the crack IS this slide's wedge -->
      <rect x="540" y="620" width="62" height="340" fill="${C.bulb}"/>
      <rect x="540" y="700" width="62" height="8" fill="${C.duvet}"/>
      <rect x="540" y="800" width="62" height="8" fill="${C.duvet}"/>
      <rect x="540" y="900" width="62" height="8" fill="${C.duvet}"/>
      <rect x="548" y="668" width="16" height="32" rx="3" fill="none" stroke="${C.duvet}" stroke-width="3"/>
      <rect x="570" y="776" width="22" height="24" rx="3" fill="none" stroke="${C.duvet}" stroke-width="3"/>
      <rect x="548" y="866" width="26" height="34" rx="3" fill="none" stroke="${C.duvet}" stroke-width="3"/>
      <!-- door thickness + face -->
      <polygon points="602,580 626,576 626,1004 602,1000" fill="${C.ghost}"/>
      <polygon points="626,576 1000,600 1000,980 626,1004" fill="${C.duvet}"/>
      <polygon points="648,602 982,620 982,960 648,978" fill="none" stroke="${C.ghost}" stroke-width="2" opacity=".8"/>
      <!-- hinges -->
      <rect x="988" y="640" width="18" height="46" rx="4" fill="${C.ghost}"/>
      <rect x="988" y="910" width="18" height="46" rx="4" fill="${C.ghost}"/>
      <!-- dial -->
      <ellipse cx="760" cy="790" rx="78" ry="90" fill="${C.dark}" stroke="${C.ghost}" stroke-width="3"/>
      <ellipse cx="760" cy="790" rx="62" ry="72" fill="none" stroke="${C.ghost}" stroke-width="2"/>
      ${ticks}
      <line x1="760" y1="790" x2="${760+44*Math.cos(-1.05)}" y2="${790+52*Math.sin(-1.05)}" stroke="${C.milk}" stroke-width="7" stroke-linecap="round"/>
      <circle cx="760" cy="790" r="8" fill="${C.milk}"/>
      <!-- lever handle -->
      <rect x="884" y="720" width="16" height="140" rx="8" fill="${C.milk}"/>
      <!-- tag on the door shelf -->
      <circle cx="572" cy="652" r="16" fill="none" stroke="${C.alarm}" stroke-width="4"/>
    </g>
    ${leader('M596 604 L604 654',606,657)}
  `, `<linearGradient id="spill4" gradientUnits="userSpaceOnUse" x1="290" y1="0" x2="160" y2="0"><stop offset="0" stop-color="${C.bulb}"/><stop offset="1" stop-color="${C.bulb}" stop-opacity=".1"/></linearGradient>
      <filter id="blur4" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="20"/></filter>`);
  slides.push(`
<div class="slide">
  <div class="numeral">3</div>
  ${ghostRoute(4,[T4])}
  ${illo}
  ${header('02:10')}
  ${rule}
  ${head}
  <div class="intel" style="left:72px;top:506px">DOOR SHELF IS THE WARMEST SEAT.
NOTHING WORTH TAKING LIVES THERE.</div>
  <div class="stack" style="left:276px;top:846px;color:${C.duvet}">Convenient.
Suspicious.
Proceed.</div>
  <p class="body">Door open a crack.
Light already on.</p>
  <div class="counter">04 / 09</div>
</div>`);
}

/* ---------- SLIDE 5 : THE TARGET ---------- */
{
  const W=620;
  const head = `<div class="head"><span class="step">STEP 4.</span><span class="name">THE TARGET.</span></div>`;
  const body = `<p class="body">Bottom shelf, back.
Small carton. Moved down.
Glass already out. Don't ask.</p>`;
  const illo = svgFull('illo', `
    <!-- fridge: the interior is the light source; the left compartment sits a shade darker so the door's edge still reads -->
    <rect x="400" y="500" width="600" height="500" rx="22" fill="${C.duvet}"/>
    <rect x="424" y="514" width="552" height="462" rx="8" fill="url(#fridgeLight)"/>
    <rect x="424" y="514" width="552" height="462" rx="8" fill="none" stroke="${C.ghost}" stroke-width="2" opacity=".6"/>
    <rect x="424" y="630" width="552" height="9" fill="${C.duvet}"/>
    <rect x="424" y="860" width="552" height="9" fill="${C.duvet}"/>
    <!-- top shelf, dimmed -->
    <g fill="none" stroke="${C.ghost}" stroke-width="3" stroke-linejoin="round" opacity=".85">
      <path d="M458 630 V566 Q458 548 474 544 V530 H502 V544 Q518 548 518 566 V630 Z"/>
      <path d="M536 630 V578 Q536 562 552 558 V538 H580 V558 Q596 562 596 578 V630 Z"/>
      <path d="M640 630 V572 L690 538 L740 572 V630 Z"/><line x1="640" y1="572" x2="740" y2="572"/>
      <path d="M822 630 V556 L862 528 L902 556 V630 Z"/><line x1="822" y1="556" x2="902" y2="556"/><rect x="836" y="578" width="52" height="36" rx="3"/>
    </g>
    <!-- low containers on the bottom shelf -->
    <g fill="none" stroke="${C.ghost}" stroke-width="3" opacity=".85">
      <rect x="450" y="816" width="72" height="44" rx="8"/><rect x="540" y="822" width="58" height="38" rx="6"/><rect x="616" y="812" width="96" height="48" rx="10"/>
      <line x1="616" y1="828" x2="712" y2="828"/>
    </g>
    <!-- moved-down arrow: ends on the target circle's rim -->
    <path d="M902 584 C 962 602 972 640 942 664" fill="none" stroke="${C.duvet}" stroke-width="4" stroke-dasharray="12 9"/>
    <path d="M961 656 L942 664 L945 644" fill="none" stroke="${C.duvet}" stroke-width="4" stroke-linejoin="round" stroke-linecap="round"/>
    <!-- REACH -->
    <path d="M424 800 H806" stroke="${C.alarm}" stroke-width="3" stroke-dasharray="12 9"/>
    <path d="M424 790 V810 M806 790 V810" stroke="${C.alarm}" stroke-width="3"/>
    <!-- target carton -->
    ${carton(872,860,120,150,40,{level:.62})}
    <circle cx="872" cy="770" r="108" fill="none" stroke="${C.alarm}" stroke-width="4"/>
    <!-- counter -->
    <rect x="72" y="860" width="288" height="40" rx="6" fill="${C.duvet}"/>
    <rect x="86" y="900" width="18" height="100" fill="${C.duvet}"/>
    <rect x="328" y="900" width="18" height="100" fill="${C.duvet}"/>
    <!-- stool, ghost outline (planted) -->
    <g fill="none" stroke="${C.ghost}" stroke-width="3" stroke-linejoin="round">
      <rect x="150" y="940" width="90" height="24" rx="4"/><path d="M162 964 L150 1000 M228 964 L240 1000"/><path d="M158 984 H232"/>
    </g>
    <!-- glass -->
    <polygon points="160,700 240,700 228,860 172,860" fill="${C.milk}" fill-opacity=".14" stroke="${C.milk}" stroke-width="6" stroke-linejoin="round"/>
    ${straw([[196,846],[206,708],[216,692],[206,676],[218,660],[296,598]])}
  `, `<linearGradient id="fridgeLight" gradientUnits="userSpaceOnUse" x1="640" y1="0" x2="424" y2="0"><stop offset="0" stop-color="${C.bulb}"/><stop offset="1" stop-color="${C.bulb}" stop-opacity=".78"/></linearGradient>`);
  slides.push(`
<div class="slide">
  <div class="numeral">4</div>
  ${wedge(W)}
  ${ghostRoute(5,[T4,T5])}
  ${illo}
  ${header('02:11')}
  ${rule}
  ${head}
  ${body}
  ${lit(W, head + `<div class="clock">02:11</div>` + body)}
  ${label(430,782,'REACH — 0.96 m',{color:C.duvet,extra:'text-transform:none;'})}
  <div class="intel" style="left:430px;top:882px;color:${C.duvet};letter-spacing:.03em">COLDEST SHELF. ONE POUR.
NEVER FROM THE CARTON.</div>
  <div class="counter">05 / 09</div>
</div>`);
}

/* ---------- SLIDE 6 : ABORT ---------- */
{
  const W=740;
  const head = `<div class="head"><span class="step">STEP 5.</span><span class="name">ABORT.</span></div>`;
  const body = `<p class="body">Couch is empty. Blanket down.
Thirty seconds. Nothing.
Proceed.</p>`;
  const DX=480, DR=DX+360;
  const illo = svgFull('illo', `
    <path d="M72 1000 H1008" stroke="${C.duvet}" stroke-width="6"/>
    <rect x="${DX}" y="500" width="360" height="500" fill="${C.dark}"/>
    <!-- far couch: Duvet with a Ghost Light keyline so it survives a dim phone -->
    <g stroke="${C.ghostl}" stroke-width="2" stroke-opacity=".7" fill="${C.duvet}">
      <rect x="520" y="762" width="280" height="66" rx="16"/>
      <rect x="520" y="822" width="280" height="46" rx="10"/>
      <rect x="504" y="784" width="40" height="86" rx="18"/>
      <rect x="776" y="784" width="40" height="86" rx="18"/>
      <rect x="534" y="868" width="16" height="14"/><rect x="770" y="868" width="16" height="14"/>
    </g>
    <rect x="657" y="770" width="5" height="52" fill="${C.dark}"/>
    <!-- blanket on the floor -->
    <path d="M568 966 L594 914 L636 934 L676 896 L706 928 L748 912 L754 966 Z" fill="${C.milk}"/>
    <path d="M598 950 Q656 928 732 948" fill="none" stroke="${C.ghost}" stroke-width="2"/>
    <!-- doorway -->
    <path d="M${DX} 1000 V500 H${DR} V1000" fill="none" stroke="${C.duvet}" stroke-width="16"/>
    <path d="M${DX+18} 1000 V518 H${DR-18} V1000" fill="none" stroke="${C.ghost}" stroke-width="2" opacity=".7"/>
    <circle cx="660" cy="796" r="100" fill="none" stroke="${C.alarm}" stroke-width="4"/>
    ${leader('M300 636 L560 778',562,780)}
  `);
  slides.push(`
<div class="slide">
  <div class="numeral">5</div>
  ${wedge(W)}
  ${ghostRoute(6,[T4,T5])}
  ${illo}
  ${header('02:12')}
  ${rule}
  ${head}
  ${body}
  ${lit(W, head + `<div class="clock">02:12</div>` + body)}
  ${label(72,620,'GUARD — NOT HERE.')}
  <div class="counter">06 / 09</div>
</div>`);
}

/* ---------- SLIDE 7 : THE POUR ---------- */
{
  const W=820;
  const head = `<div class="head" style="left:230px"><span class="step">STEP 6.</span><span class="name">THE POUR.</span></div>`;
  const body = `<p class="body" style="left:230px">Two hands. Slow.
Creak in the hall. Not yours.
Freeze. Wait. Pour.</p>`;
  const lab = label(1008,536,'SET OF FOUR.\nTHE LAST ONE.',{right:true});
  const illo = svgFull('illo', `
    <circle cx="-40" cy="760" r="200" fill="none" stroke="${C.alarm}" stroke-width="3" opacity=".9"/>
    <circle cx="-40" cy="760" r="320" fill="none" stroke="${C.alarm}" stroke-width="3" opacity=".9"/>
    <!-- glass -->
    <polygon points="738,800 822,800 808,1000 752,1000" fill="${C.milk}" fill-opacity=".28" stroke="${C.duvet}" stroke-width="6" stroke-linejoin="round"/>
    <polygon points="748,935 812,935 808,1000 752,1000" fill="${C.milk}" stroke="${C.duvet}" stroke-width="4" stroke-linejoin="round"/>
    <!-- pour: Milk with a Duvet keyline so it holds inside the light -->
    <path d="M546 640 C 660 692 745 800 766 935 L 790 935 C 776 792 692 664 558 626 Z" fill="${C.milk}" stroke="${C.duvet}" stroke-width="4" stroke-linejoin="round"/>
    <path d="M752 935 H808" stroke="${C.milk}" stroke-width="6"/>
    ${straw([[772,990],[788,806],[798,790],[788,774],[800,758],[882,692]])}
    <!-- tilted carton, level milk in the window -->
    <g transform="rotate(40 440 640)">
      <path d="M358 565 L440 510 L522 565 Z" fill="${C.milk}" stroke="${C.duvet}" stroke-width="4" stroke-linejoin="round"/>
      <rect x="358" y="565" width="164" height="205" rx="10" fill="${C.milk}" stroke="${C.duvet}" stroke-width="4"/>
      <rect x="424" y="522" width="32" height="26" fill="${C.milk}" stroke="${C.duvet}" stroke-width="3"/>
      <path d="M492 545 L540 545 L522 565 Z" fill="${C.milk}" stroke="${C.duvet}" stroke-width="3" stroke-linejoin="round"/>
      <rect x="380" y="620" width="120" height="120" rx="6" fill="${C.duvet}"/>
    </g>
    <rect x="280" y="690" width="320" height="220" fill="${C.milk}" clip-path="url(#panel7)"/>
    ${leader('M866 594 L802 792',800,796)}
  `, `<clipPath id="panel7"><rect x="388" y="628" width="104" height="104" rx="4" transform="rotate(40 440 640)"/></clipPath>`);
  slides.push(`
<div class="slide">
  <div class="numeral">6</div>
  ${wedge(W)}
  ${ghostRoute(7,[T4,T5],.16)}
  ${illo}
  <div class="file">MILK RUN // FILE 0902</div><div class="clock alarm">02:13</div>
  ${rule}
  ${head}
  ${lab}
  ${body}
  ${lit(W, head + lab + body)}
  <div class="freeze">FREEZE.</div>
  <div class="counter">07 / 09</div>
</div>`);
}

/* ---------- SLIDE 8 : EXTRACTION ---------- */
{
  const W=90;
  const head = `<div class="head"><span class="step">STEP 7.</span><span class="name">EXTRACTION.</span></div>`;
  const illo = svgFull('illo', `
    <!-- witness: two eyes, slit pupils, soft halos -->
    <circle cx="200" cy="562" r="76" fill="url(#halo8)"/><circle cx="248" cy="562" r="76" fill="url(#halo8)"/>
    <ellipse cx="200" cy="562" rx="15" ry="12" fill="${C.bulb}"/><ellipse cx="248" cy="562" rx="15" ry="12" fill="${C.bulb}"/>
    <ellipse cx="200" cy="562" rx="3" ry="9" fill="${C.dark}"/><ellipse cx="248" cy="562" rx="3" ry="9" fill="${C.dark}"/>
    ${leader('M292 562 H272',270,562)}
    <!-- sink on a Milk counter line, tap above, one drop -->
    <rect x="400" y="820" width="500" height="180" rx="34" fill="${C.duvet}"/>
    <path d="M376 820 H924" stroke="${C.milk}" stroke-width="3"/>
    <path d="M620 820 V782 A46 46 0 0 1 712 782 V804" fill="none" stroke="${C.milk}" stroke-width="7" stroke-linecap="round"/>
    <rect x="608" y="806" width="24" height="14" rx="3" fill="${C.milk}"/>
    <path d="M712 826 C706 838 704 842 704 846 A8 8 0 0 0 720 846 C720 842 718 838 712 826 Z" fill="${C.milk}"/>
    <!-- glass on its side -->
    <polygon points="500,905 670,888 670,972 500,955" fill="none" stroke="${C.milk}" stroke-width="3" stroke-linejoin="round"/>
    <line x1="670" y1="888" x2="670" y2="972" stroke="${C.milk}" stroke-width="3"/>
    ${straw([[530,934],[676,918],[692,910],[684,896],[698,886],[770,870]],{w:8})}
    <!-- retreat: from the sink back down the base path to where the towel sat on slide 3 (320,700) -->
    <path d="M400 900 C 412 830 400 770 366 744" fill="none" stroke="${C.milk}" stroke-width="4" stroke-dasharray="14 10" stroke-linecap="round"/>
    <circle cx="320" cy="700" r="60" fill="none" stroke="${C.alarm}" stroke-width="4"/>
  `, `<radialGradient id="halo8"><stop offset="0" stop-color="${C.bulb}" stop-opacity=".6"/><stop offset=".35" stop-color="${C.bulb}" stop-opacity=".22"/><stop offset=".7" stop-color="${C.bulb}" stop-opacity=".06"/><stop offset="1" stop-color="${C.bulb}" stop-opacity="0"/></radialGradient>`);
  slides.push(`
<div class="slide">
  <div class="numeral">7</div>
  ${wedge(W)}
  ${ghostRoute(8,[T4,T5])}
  ${illo}
  ${header('02:15')}
  ${rule}
  ${head}
  ${label(300,574,'WITNESS: 1. NOT A THREAT.')}
  ${label(72,798,'TOWEL — GONE.',{color:C.alarm})}
  <p class="body">Drink standing. Rinse cold.
Hot water sets the protein.
A film is a confession.</p>
  <div class="counter" style="right:112px">08 / 09</div>
</div>`);
}

/* ---------- SLIDE 9 : CLOSE ---------- */
{
  const D=C.duvet;
  const illo = svgFull('illo', `
    <!-- floor -->
    <path d="M72 1056 H1008" stroke="${D}" stroke-width="4"/>
    <!-- the guard's own diagram: fridge position at the right edge, along the floor under the doorway, past the stool, to where the sink was -->
    <g fill="none" stroke="${D}" stroke-width="3" stroke-dasharray="14 10" stroke-linecap="round">
      <path d="M1040 1082 H300"/>
      <path d="M588 1082 V1030 M702 1082 V1030"/>
      <path d="M380 1082 V1030"/>
    </g>
    <circle cx="1040" cy="1082" r="6" fill="${D}"/>
    <circle cx="300" cy="1082" r="6" fill="${D}"/>
    <circle cx="380" cy="1028" r="4" fill="${D}"/><circle cx="588" cy="1028" r="4" fill="${D}"/><circle cx="702" cy="1028" r="4" fill="${D}"/>
    <!-- counter -->
    <rect x="72" y="850" width="728" height="50" rx="6" fill="${D}"/>
    <rect x="86" y="900" width="20" height="156" fill="${D}"/>
    <rect x="766" y="900" width="20" height="156" fill="${D}"/>
    <!-- stool, real now -->
    <rect x="600" y="988" width="90" height="24" rx="4" fill="${D}"/>
    <polygon points="606,1012 620,1012 612,1056 598,1056" fill="${D}"/>
    <polygon points="670,1012 684,1012 692,1056 678,1056" fill="${D}"/>
    <rect x="612" y="1030" width="66" height="6" fill="${D}"/>
    <!-- glass 2 -->
    <polygon points="612,720 678,720 668,850 622,850" fill="none" stroke="${D}" stroke-width="6" stroke-linejoin="round"/>
    ${straw([[638,842],[652,728],[660,714],[652,700],[662,686],[722,632]],{color:D,under:C.bulb,w:8})}
    <circle cx="645" cy="785" r="86" fill="none" stroke="${C.alarm}" stroke-width="4"/>
    ${leader('M518 800 H552',556,800,D)}
    <!-- doorway + the guard -->
    <path d="M860 1056 V560 H1000 V1056" fill="none" stroke="${D}" stroke-width="16"/>
    <g fill="${D}">
      <g transform="rotate(7 930 616)"><circle cx="930" cy="614" r="30"/></g>
      <rect x="918" y="638" width="24" height="24"/>
      <rect x="896" y="652" width="68" height="192" rx="30"/>
      <rect x="904" y="842" width="24" height="208" rx="10"/>
      <rect x="932" y="842" width="24" height="208" rx="10"/>
      <rect x="898" y="1036" width="30" height="20" rx="6"/><rect x="932" y="1036" width="34" height="20" rx="6"/>
    </g>
    <rect x="932" y="842" width="4" height="196" fill="${C.bulb}"/>
    <!-- folded arm: upper arm drops to an elbow that breaks the silhouette, forearm crosses the torso -->
    <path d="M908 672 L884 754 L962 746" fill="none" stroke="${D}" stroke-width="30" stroke-linecap="round" stroke-linejoin="round"/>
    <path d="M898 692 L886 738" fill="none" stroke="${C.bulb}" stroke-width="3" stroke-linecap="round"/>
    <path d="M892 734 L958 728" fill="none" stroke="${C.bulb}" stroke-width="3" stroke-linecap="round"/>
    <!-- leader from the evidence tag to the figure -->
    <path d="M930 1102 V1066" fill="none" stroke="${C.alarm}" stroke-width="2" stroke-dasharray="8 6"/>
    ${dot(930,1064,C.alarm,4)}
  `);
  slides.push(`
<div class="slide s9">
  ${illo}
  ${header('02:16')}
  <div class="close">1,204 JOBS.<br>0 ARRESTS.</div>
  <p class="body9">Guard: awake. Confirmed.
Towel. Carton. Stool.
Never asked.</p>
  ${label(536,802,'GLASS 2 — TOMORROW.',{color:D,right:true})}
  <div class="tag"><div>SUSPECT: <span class="redact"></span></div><div class="send">SEND THIS TO WHOEVER PUT THE GLASS OUT.</div></div>
  <div class="counter">09 / 09</div>
</div>`);
}

const css = `
:root{--dark:${C.dark};--bulb:${C.bulb};--milk:${C.milk};--duvet:${C.duvet};--alarm:${C.alarm};--ghostl:${C.ghostl};--ghost:${C.ghost}}
*{box-sizing:border-box}
body{margin:0;background:#000;display:flex;flex-direction:column;align-items:flex-start;gap:0}
.slide{width:1080px;height:1350px;position:relative;overflow:hidden;background:var(--dark);font-family:'IBM Plex Mono','Courier New',monospace;color:var(--milk);flex:none}
.slide>*{position:absolute;margin:0}
.full{left:0;top:0;width:1080px;height:1350px;display:block}
.numeral{z-index:1;font-family:Anton,Impact,'Arial Narrow Bold',sans-serif;font-size:900px;line-height:1;right:72px;top:271px;color:transparent;-webkit-text-stroke:2px rgba(58,70,102,.65)}
.glow{z-index:2;inset:0;filter:blur(34px);opacity:.5}
.glow>div{position:absolute;inset:0;background:var(--bulb)}
.wedge{z-index:2;inset:0;background:linear-gradient(250deg,#FFD43B 0%,#FFD43B 55%,rgba(255,212,59,.88) 100%)}
.route{z-index:3}
.illo{z-index:4}
.rule{z-index:5;left:0;top:0;width:1080px;height:1350px;position:absolute}
.file{z-index:6;left:72px;top:160px;font-size:26px;line-height:1;color:var(--ghost);letter-spacing:.02em}
.clock{z-index:6;right:72px;top:120px;font-size:72px;line-height:1;font-weight:500;color:var(--milk)}
.clock.alarm{color:var(--alarm);background:var(--dark);padding:8px 16px;margin:-8px -16px;border-radius:8px}
.counter{z-index:6;right:72px;top:1241px;font-size:26px;line-height:1;color:var(--ghost);letter-spacing:.02em}
.head{z-index:6;left:72px;top:222px;font-family:Anton,Impact,'Arial Narrow Bold',sans-serif;color:var(--milk);line-height:.88;letter-spacing:-.01em;white-space:nowrap}
.head .step{display:block;font-size:96px}
.head .name{display:block;font-size:190px}
.hook{z-index:6;right:72px;top:250px;font-family:Anton,Impact,sans-serif;font-size:210px;line-height:.88;letter-spacing:-.01em;color:var(--milk);text-align:right;white-space:nowrap}
.sub{z-index:6;right:72px;top:834px;font-family:Anton,Impact,sans-serif;font-size:110px;line-height:.88;color:var(--bulb);text-align:right;white-space:nowrap;text-transform:lowercase}
.lit{z-index:7;inset:0;pointer-events:none}
.lit>*{position:absolute;margin:0}
.lit .head,.lit .hook,.lit .sub,.lit .label,.lit .intel,.lit .clock,.lit .body{color:var(--duvet)!important}
.lit .full{left:0;top:0}
.body{z-index:6;left:72px;bottom:157px;font-size:44px;line-height:1.35;color:var(--milk);white-space:pre;max-width:800px}
.label{z-index:6;font-size:36px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;line-height:1.25;white-space:pre}
.label.vert{writing-mode:vertical-rl;letter-spacing:.1em}
.intel{z-index:6;font-size:36px;font-weight:500;letter-spacing:.06em;text-transform:uppercase;line-height:1.25;white-space:pre;color:var(--ghostl)}
.stack{z-index:6;font-size:40px;line-height:1.2;white-space:pre;color:var(--milk)}
.freeze{z-index:6;left:72px;top:480px;font-family:Anton,Impact,sans-serif;font-size:190px;line-height:.8;color:var(--alarm);writing-mode:vertical-rl;white-space:nowrap;letter-spacing:-.01em}
/* slide 9 */
.s9{background:var(--bulb);color:var(--duvet)}
.s9 .file,.s9 .counter{color:var(--duvet);opacity:.75}
.s9 .clock{color:var(--duvet)}
.close{z-index:6;left:72px;top:246px;font-family:Anton,Impact,sans-serif;font-size:170px;line-height:.96;letter-spacing:-.01em;color:var(--duvet);white-space:nowrap}
.body9{z-index:6;left:72px;top:590px;font-size:40px;line-height:1.35;color:var(--duvet);white-space:pre}
.tag{z-index:6;left:72px;top:1104px;width:936px;height:130px;border:3px solid var(--alarm);padding:16px 24px;font-size:36px;font-weight:500;letter-spacing:.06em;line-height:1.3;color:var(--duvet);white-space:nowrap}
.tag .redact{display:inline-block;width:190px;height:28px;background:var(--duvet);vertical-align:-3px}
.tag .send{color:var(--duvet);letter-spacing:0}
`;

const html = `<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>MILK RUN — carousel</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>${css}</style></head><body>${slides.join('\n')}
</body></html>`;
fs.writeFileSync(OUT, html);
console.log('wrote', OUT, html.length, 'bytes');
