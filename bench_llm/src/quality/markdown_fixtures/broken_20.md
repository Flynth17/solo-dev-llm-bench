# Broken Markdown Challenge

This document contains twenty deliberately introduced defects labeled MD-01 through MD-20.

## Heading Hierarchy Block
### Third level heading below it
##### Fifth level heading skips two levels here

## Missing Space After Hash Block
#NoSpaceAfterHash this line has no space after the marker

## Multiple H1 Heading Block

# This is a second top-level title that should not exist

Some explanatory text.

## Shared Notes Heading
First occurrence of shared notes text.

Text directly above this heading without any blank line separating them.
## Heading With No Blank Line Before It
Body text right after the heading with no preceding blank line either.

## Inconsistent List Markers Block

- dash item here
* asterisk item here
+ plus item here

## Ordered Numbering Block

1. First numbered step
3. Second numbered step
4. Third numbered step

Text directly above the list with no blank line in between at all.
- item alpha first
- item beta second
Text directly below the list with no blank line after it either.

## Trailing Whitespace Block
This paragraph line has trailing spaces that should be removed now   

## Excessive Blank Lines Block



Some text after many consecutive blank lines above.

## Bare URL Block
Visit https://example.com/docs for more details and information.

## Unlabeled Code Fence Block

```
value = 1
```
Text after the unlabeled code fence here.

## Inconsistent Fence Style Block

~~~python
code_here = 2
~~~
Text after the tilde fenced block here.

## Emphasis As Heading Block
**This Should Be A Heading Not Bold Text At All**
Body text follows below it.

## Shared Notes Heading
Duplicate occurrence text here.

## Heading With Trailing Punctuation.
Body after punctuation heading.

## Orphaned List Item Indentation Block

- first sibling item here
    - oddly nested second item here
- third sibling item here

## Malformed Table Without Separator Block

| Name | Value |
| Alpha | 1 |
| Beta | 2 |

## Inconsistent Table Column Count Block

| A | B |
| --- | --- |
| x | y |
| z | w | v |

## Hard Tab Indentation Block

	Tab indented item line should use spaces not a tab.