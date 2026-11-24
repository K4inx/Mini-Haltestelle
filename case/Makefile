# Mini-Haltestelle — Gehäuse
#   make stl   alle Teile als STL nach stl/
#   make png   Vorschaubilder nach vorschau/ (braucht xvfb-run auf Servern ohne Bildschirm)
SCAD := mini-haltestelle.scad
PARTS := front back lichtleiter
OPENSCAD ?= openscad
XVFB := $(shell command -v xvfb-run >/dev/null && echo "xvfb-run -a")

.PHONY: stl png clean
stl: $(PARTS:%=stl/%.stl)

stl/%.stl: $(SCAD)
	@mkdir -p stl
	$(OPENSCAD) -q -D 'part="$*"' -o $@ $<

png: $(SCAD)
	@mkdir -p vorschau
	$(XVFB) $(OPENSCAD) -q -D 'part="alle"' --imgsize=900,700 --camera=90,320,120,0,0,25 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/vorn.png $<
	$(XVFB) $(OPENSCAD) -q -D 'part="alle"' --imgsize=900,700 --camera=-120,-300,140,0,0,25 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/hinten.png $<
	$(XVFB) $(OPENSCAD) -q -D 'part="alle"' --imgsize=700,600 --camera=350,0,30,0,0,25 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/seite.png $<
	$(XVFB) $(OPENSCAD) -q -D 'part="front"' --imgsize=900,700 --camera=0,0,0,180,0,0,170 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/front-innen.png $<

clean:
	rm -rf stl
