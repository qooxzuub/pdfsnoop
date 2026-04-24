- when normalizing, make hex strings readable if possible

- fix indirect object editing: tree update is not right yet 

- undo/redo

- open file

- revert file

- pdf reference info

- validation??

- jump to object by objgen

- forward/back for tree navigation

- copy to clipboard. paste??

- pdfsnoop file.pdf --object 1234

- add dictionary keys and array entries. 

- should string editing accept hex? or a separate 'type'?

- when you select a Tj or TJ operator in a content stream and the operand is a hex string, the metadata pane could look up the font in /Resources/Font, find its /ToUnicode CMap, and show you what the string actually says.


- follow /Do ?  when the cursor is on a Do operator in the disassembled stream, pressing Enter could navigate the tree to the referenced XObject's stream. The XObject name (like /G3 before the gs, or whatever precedes the Do) is the key in /Resources/XObject.
