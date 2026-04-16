TEX = /Library/TeX/texbin/pdflatex
BIB = /Library/TeX/texbin/bibtex
FLAGS = -interaction=nonstopmode

all: main.pdf

main.pdf: main.tex references.bib
	$(TEX) $(FLAGS) main.tex
	$(BIB) main
	$(TEX) $(FLAGS) main.tex
	$(TEX) $(FLAGS) main.tex

clean:
	rm -f main.aux main.bbl main.blg main.log main.out main.toc

distclean: clean
	rm -f main.pdf

arxiv: main.pdf
	zip gen-pilot-arxiv.zip main.tex main.bbl references.bib

.PHONY: all clean distclean arxiv
