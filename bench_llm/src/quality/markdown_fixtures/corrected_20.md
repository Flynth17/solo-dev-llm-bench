# Broken Markdown Challenge

This document contains twenty deliberately introduced defects labeled MD-01 through MD-20.

## Heading Hierarchy Block
### Third level heading below it
#### Fourth level heading continues properly

## Missing Space After Hash Block
This heading should have a space after the marker.

## Multiple H1 Heading Block

This extra heading was removed to avoid multiple H1 titles in one document.

Some explanatory text.

## Shared Notes Heading
First occurrence of shared notes text.

Text directly above this heading, now separated by a blank line from it.

## Heading With No Blank Line Before It
Body text right after the heading with no preceding blank line either.

## Inconsistent List Markers Block

- dash item here
- asterisk item here
- plus item here

## Ordered Numbering Block

1. First numbered step
2. Second numbered step
3. Third numbered step

Text directly above the list, now separated by a blank line from it.

- item alpha first
- item beta second

Text directly below the list, now separated by a blank line from it.

## Trailing Whitespace Block
This paragraph line no longer has trailing spaces to remove.

## Excessive Blank Lines Block

Some text after many consecutive blank lines above.

## Bare URL Block
Visit [our docs](https://example.com/docs) for more details and information.

## Unlabeled Code Fence Block

```python
value = 1
```
Text after the labeled code fence here.

## Inconsistent Fence Style Block

```python
code_here = 2
```
Text after the fenced block here.

## Emphasis As Heading Block

This line is plain text rather than emphasis styled like a heading.
Body text follows below it.

## Additional Resources Section
Second occurrence, now with unique wording and not a duplicate title.

## Heading Without Trailing Punctuation
Body after punctuation heading.

## Orphaned List Item Indentation Block

- first sibling item here
- oddly nested second item here
- third sibling item here

## Malformed Table Without Separator Block

| Name | Value |
| --- | --- |
| Alpha | 1 |
| Beta | 2 |

## Inconsistent Table Column Count Block

| A | B |
| --- | --- |
| x | y |
| z | w |

## Hard Tab Indentation Block

This item is indented with spaces instead of a tab character.
