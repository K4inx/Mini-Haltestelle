// Mini-Haltestelle — parametrisches Gehäuse für Pi Zero W + Pimoroni Display HAT Mini
//
// Schlichter Schreibtisch-Kasten, leicht nach hinten geneigt, auf schmalem Sockel. Einzige
// Andeutung: ein kleines rundes Schild oben rechts mit vertieftem „H" — alles einfarbig.
// Das USB-Kabel (gewinkelter Micro-USB-Stecker) läuft durch eine Kerbe im Deckel nach hinten.
// Teile einzeln:   openscad -D 'part="front"' -o stl/front.stl mini-haltestelle.scad
// Alle STLs:       make stl          Vorschaubilder: make png
//
// ALLE MASSE OBEN ALS VARIABLEN. Werte mit „(prüfen)" sind nicht aus einer offiziellen
// Maßzeichnung bestätigt — vor dem ersten Druck mit Messschieber nachmessen und hier anpassen.
// Koordinaten: x = Breite (negativ = VON VORN GESEHEN LINKS), y = Höhe (oben +),
// z = Tiefe (0 = Vorderseite, + nach hinten). Ursprung: Mitte der Platinen in x/y.
// (Intern wird dafür an x gespiegelt, weil man von −z auf die Front schaut.)

/* [Teil] */
part = "alle"; // [alle, explosion, front, back, lichtleiter]

/* [Pi Zero W (offiziell)] */
zero_w = 65;
zero_h = 30;
zero_r = 3;
pcb_t = 1.6;
hole_inset = 3.5;         // Bohrungen 3,5 mm vom Rand, Ø 2,75
hole_d = 2.75;
usb_pwr_x = 54;           // Micro-USB Strom, Mitte ab linker Kante (Unterseite der Platine)
sd_y = 16.9;              // microSD, Mitte ab unterer Kante (linke Schmalseite)

/* [Display HAT Mini (prüfen)] */
hat_w = 65.5;             // (prüfen) laut Projektvorgabe ca. 65,5 × 35
hat_h = 35;               // (prüfen)
hat_dy = 0;               // (prüfen) Versatz HAT-Mitte zu Zero-Mitte in y
win_w = 40.8;             // sichtbare Displayfläche (prüfen)
win_h = 30.6;             // (prüfen)
win_dx = 0;               // (prüfen) Versatz Displaymitte zur Platinenmitte
win_dy = 0;               // (prüfen)
win_margin = 0.6;         // Fenster etwas größer als die aktive Fläche
disp_front_h = 4.2;       // (prüfen) Glasoberkante über HAT-Platine
btn_dx = 28.0;            // (prüfen) Taster-Mitte ab Platinenmitte in x (links −, rechts +)
btn_dy = 7.0;             // (prüfen) A/X oben +, B/Y unten −
btn_h = 1.6;              // (prüfen) Taster-Oberkante über HAT-Platine
led_x = -28.0;            // (prüfen) RGB-LED Position
led_y = 0.0;              // (prüfen)
stack_h = 17;             // Unterseite Zero bis Glasoberkante (mit 10-mm-Abstandshaltern)

/* [Gehäuse] */
wall = 2.0;
front_t = 1.6;            // Frontplatte; Glas liegt innen an
clr = 0.5;                // Spiel rund um die Platinen
corner_r = 4;
boss_space = 6;           // Platz oben/unten für die Deckelschrauben
back_gap = 3.0;           // Luft hinter dem Zero (Kabel, Lötstellen)
back_t = 2.0;
lip = 1.6;                // Deckelfalz
screw_d = 1.8;            // Kernloch für M2-Blechschraube (Deckel)
screw_head_d = 4.0;
m25_d = 2.8;              // Durchgang M2,5 (Deckel → Abstandshalter)

/* [Taster-Stößel] */
tab_w = 6;
tab_l = 9;
slot = 0.7;               // Schlitz um die federnde Zunge
nub_d = 3.0;
nub_clear = 0.3;          // Luft zwischen Noppe und Taster

/* [Stand und Schild] */
tilt = 10;                // Neigung nach hinten (Grad)
base_h = 7;               // Sockelhöhe vorn; nach hinten läuft der Keil flach aus
base_inset = 8;           // Sockel seitlich schmaler als der Kasten → wirkt schwebend
base_ext = 10;            // Sockel ragt hinten über den Deckel hinaus (kippt nicht beim Drücken)
base_min = 1.2;           // Mindestdicke am hinteren Ende
cable_w = 12;             // Kerbe im Deckel für das Kabel
cable_h = 8;
sign_d = 15;              // kleines rundes Schild oben rechts
sign_rise = 5;            // so weit ragt es über die Oberkante
sign_t = front_t;        // bündig mit der Frontplatte, ragt innen nicht vor
h_depth = 0.6;            // „H" vertieft (einfarbig)
sign_inset = 10;          // Abstand Schildmitte zur rechten Außenkante

$fn = 64;

// ------------------------------------------------------------------ Abgeleitet
in_w = max(zero_w, hat_w) + 2 * clr;
in_h = max(zero_h, hat_h) + 2 * clr + 2 * boss_space;
in_d = front_t + stack_h + back_gap;          // Vorderseite bis Deckel-Innenfläche
out_w = in_w + 2 * wall;
out_h = in_h + 2 * wall;
zero_z = front_t + stack_h - pcb_t;            // Unterseite Zero = stack_h hinter Glas... vereinfacht
hat_z = front_t + disp_front_h;                // HAT-Platine (Oberseite) hinter Frontplatte
zero_holes = [for (sx = [-1, 1], sy = [-1, 1]) [sx * (zero_w / 2 - hole_inset), sy * (zero_h / 2 - hole_inset)]];
btns = [[-btn_dx, btn_dy], [-btn_dx, -btn_dy], [btn_dx, btn_dy], [btn_dx, -btn_dy]];
boss_pos = [[0, in_h / 2 - boss_space / 2], [0, -in_h / 2 + boss_space / 2]];

module rbox(w, h, d, r) {
    linear_extrude(d) offset(r) offset(-r) square([w, h], center = true);
}

// ------------------------------------------------------------------ Vorderteil
module front() {
    difference() {
        union() {
            difference() {
                rbox(out_w, out_h, in_d, corner_r);
                translate([0, 0, front_t]) rbox(in_w, in_h, in_d, corner_r - wall);
            }
            // Schraubdome für den Deckel
            for (p = boss_pos) translate([p[0], p[1], front_t])
                cylinder(d = screw_head_d + 2, h = in_d - front_t - lip);
            base();
            // kleines Schild oben rechts (von vorn gesehen)
            translate([out_w / 2 - sign_inset, out_h / 2 - sign_d / 2 + sign_rise, 0]) cylinder(d = sign_d, h = sign_t);
        }
        // Displayfenster mit Fase (Rand bleibt dünn, Blick schräg frei)
        translate([win_dx, win_dy + hat_dy, -0.01]) hull() {
            linear_extrude(0.01) square([win_w + win_margin + 2 * front_t, win_h + win_margin + 2 * front_t], center = true);
            translate([0, 0, front_t + 0.02]) linear_extrude(0.01) square([win_w + win_margin, win_h + win_margin], center = true);
        }
        // Taster-Zungen: U-förmiger Schlitz, Zunge federt nach innen
        for (b = btns) translate([b[0], b[1] + hat_dy, -0.01]) tab_cut(b[1] > 0 ? 1 : -1);
        // LED-Loch (für Lichtleiter Ø 3)
        translate([led_x, led_y + hat_dy, -0.01]) cylinder(d = 3.2, h = front_t + 1);
        // Schraublöcher in den Domen
        for (p = boss_pos) translate([p[0], p[1], front_t + 1]) cylinder(d = screw_d, h = in_d);
        // vertieftes H auf dem Schild (Vorderseite = Druckbett)
        translate([out_w / 2 - sign_inset, out_h / 2 - sign_d / 2 + sign_rise, -0.01])
            mirror([1, 0, 0]) linear_extrude(h_depth + 0.01) h_letter(sign_d * 0.55);
        // microSD links
        translate([-out_w / 2 - 1, sd_y - zero_h / 2 - 7, zero_z - 3]) cube([wall + 2, 14, 5]);
        // Lüftungsschlitze oben
        for (i = [-3:3]) translate([i * 6 - 1, out_h / 2 - wall - 1, in_d * 0.45]) cube([2, wall + 2, in_d * 0.4]);
        // Mastbohrungen
    }
    // Noppen auf den Zungen (drücken auf die Taster)
    for (b = btns) translate([b[0], b[1] + hat_dy, front_t])
        cylinder(d = nub_d, h = (front_t + disp_front_h - btn_h) - front_t - nub_clear);
}

// Zunge: senkrecht, Noppe am freien Ende über dem Taster, Gelenk zeigt vom Display weg
// (dir = +1 → Gelenk oben, für die oberen Taster A/X; −1 → unten für B/Y)
module tab_cut(dir) {
    translate([0, dir * (tab_l / 2 - nub_d / 2 - 0.5), 0]) difference() {
        linear_extrude(front_t + 0.1) square([tab_w + 2 * slot, tab_l + 2 * slot], center = true);
        linear_extrude(front_t + 0.1) square([tab_w, tab_l], center = true);
        translate([0, dir * (tab_l / 2 + slot / 2), 0]) linear_extrude(front_t + 0.1)
            square([tab_w + 2 * slot + 0.02, slot + 0.02], center = true);
    }
}

// ------------------------------------------------------------------ Deckel
module back() {
    difference() {
        union() {
            rbox(out_w, out_h, back_t, corner_r);
            // Falz, der ins Vorderteil greift
            translate([0, 0, back_t]) difference() {
                rbox(in_w - 0.3, in_h - 0.3, lip, corner_r - wall);
                rbox(in_w - 0.3 - 2 * lip, in_h - 0.3 - 2 * lip, lip + 0.1, max(0.5, corner_r - wall - lip));
            }
            // Stützen zu den Abstandshaltern (Zero-Unterseite)
            for (h = zero_holes) translate([h[0], h[1] + 0, back_t]) cylinder(d = 6, h = back_gap);
        }
        // Deckelschrauben (Senkung außen)
        for (p = boss_pos) translate([p[0], p[1], -0.01]) {
            cylinder(d = screw_d + 0.6, h = back_t + lip + 1);
            cylinder(d1 = screw_head_d, d2 = screw_d + 0.6, h = 1.2);
        }
        // M2,5 durch die Stützen in die Abstandshalter
        for (h = zero_holes) translate([h[0], h[1], -0.01]) {
            cylinder(d = m25_d, h = back_t + back_gap + 1);
            cylinder(d1 = 5, d2 = m25_d, h = 1.4);
        }
        // Kerbe unten für das Kabel (gewinkelter Micro-USB-Stecker zeigt nach hinten)
        translate([usb_pwr_x - zero_w / 2 - cable_w / 2, -out_h / 2 - 1, -0.01])
            cube([cable_w, cable_h + 1 + wall, back_t + lip + 0.1]);
        // Lüftungsschlitze
        for (i = [-4:4]) translate([i * 5.5, 0, -0.01]) linear_extrude(back_t + 0.1)
            offset(0.9) offset(-0.9) square([2, in_h * 0.45], center = true);
    }
}

// Sockel: Keil unter dem Kasten. Kippt man den Kasten um tilt nach hinten, sinkt seine
// Hinterkante — also ist der Keil vorn base_h hoch und läuft nach hinten flach aus.
// Ragt base_ext über den Deckel hinaus, damit er beim Drücken der Taster nicht kippt.
module base() {
    depth = in_d + back_t + base_ext;
    back_y = -out_h / 2 - max(base_min, base_h - depth * tan(tilt));
    translate([(out_w - 2 * base_inset) / 2, 0, 0]) rotate([0, -90, 0])
        linear_extrude(out_w - 2 * base_inset) polygon([
            [0, -out_h / 2 + wall],
            [in_d + back_t, -out_h / 2 + wall],
            [depth, back_y + base_min],
            [depth, back_y],
            [0, -out_h / 2 - base_h],
        ]);
}

module h_letter(s) {
    union() {
        translate([-s / 2, -s / 2]) square([s * 0.26, s]);
        translate([s / 2 - s * 0.26, -s / 2]) square([s * 0.26, s]);
        translate([-s / 2, -s * 0.13]) square([s, s * 0.26]);
    }
}

module light_pipe() {
    cylinder(d = 3, h = front_t + disp_front_h - 0.5);
    cylinder(d = 4.2, h = 0.8);
}

// ------------------------------------------------------------------ Zusammenbau
module stack_dummy() {
    // Nur zur Ansicht: Zero, HAT, Display
    color("darkgreen") translate([0, 0, zero_z]) rbox(zero_w, zero_h, pcb_t, zero_r);
    color("black") translate([0, hat_dy, hat_z]) rbox(hat_w, hat_h, pcb_t, 2);
    color("#223") translate([win_dx, win_dy + hat_dy, front_t]) rbox(win_w + 6, win_h + 4, disp_front_h - 0.01, 1);
}

module assembly(explode = 0) {
    e = explode;
    mirror([1, 0, 0]) {
        color("#3b3f44") front();
        color("#34383c") translate([0, 0, in_d + back_t + e * 25]) mirror([0, 0, 1]) back();
        if (e == 0) stack_dummy();
    }
}

// Auf dem Tisch: um die vordere Unterkante des Sockels nach hinten kippen
module on_desk(e = 0) {
    // Drehpunkt: vordere Unterkante des Sockels; Oberkante kippt nach hinten (+z)
    rotate([90, 0, 0]) translate([0, out_h / 2 + base_h, 0]) rotate([tilt, 0, 0])
        translate([0, out_h / 2 + base_h, 0]) assembly(e);
}

// ------------------------------------------------------------------ Auswahl (Druckausrichtung)
// Für Vorschaubilder: aufgerichtet (Modell-y → z), Vorderseite zeigt nach +y
if (part == "alle") on_desk(0);
else if (part == "explosion") on_desk(1);
else if (part == "front") mirror([1, 0, 0]) front();            // Vorderseite aufs Bett
else if (part == "back") mirror([1, 0, 0]) back();              // Außenseite aufs Bett
else if (part == "lichtleiter") light_pipe();                   // klares/naturfarbenes PLA oder PETG
