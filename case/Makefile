# Mini-Haltestelle — Gehäuse
#   make stl   alle Teile als STL nach stl/
#   make png   Vorschaubilder nach vorschau/ (braucht xvfb-run auf Servern ohne Bildschirm)
SCAD := mini-haltestelle.scad
PARTS := front back mast mast_oben fuss schild schild_h lichtleiter
OPENSCAD ?= openscad
XVFB := $(shell command -v xvfb-run >/dev/null && echo "xvfb-run -a")

.PHONY: stl png clean
stl: $(PARTS:%=stl/%.stl)

stl/%.stl: $(SCAD)
	@mkdir -p stl
	$(OPENSCAD) -q -D 'part="$*"' -o $@ $<

png: $(SCAD)
	@mkdir -p vorschau
	$(XVFB) $(OPENSCAD) -q -D 'part="alle"' --imgsize=700,900 --camera=0,330,40,0,0,40 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/vorn.png $<
	$(XVFB) $(OPENSCAD) -q -D 'part="alle"' --imgsize=900,1300 --camera=110,330,90,0,0,0 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/schraeg.png $<
	$(XVFB) $(OPENSCAD) -q -D 'part="explosion"' --imgsize=900,1300 --camera=260,260,120,0,0,0 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/explosion.png $<
	$(XVFB) $(OPENSCAD) -q -D 'part="front"' --imgsize=900,700 --camera=0,0,0,180,0,0,170 --viewall --autocenter --colorscheme=Tomorrow -o vorschau/front-innen.png $<

clean:
	rm -rf stl
