"""
Parser for .qmd quiz files.

Parses quiz metadata and question data from a Quarto Markdown file
using fenced div syntax (:::: question blocks).
"""

import re
import yaml
import textwrap


def parse_qmd_quiz(content):
    """
    Parse a .qmd quiz file into quiz metadata and a list of question dicts.
    
    Returns:
        (canvas_meta, questions_data) where:
        - canvas_meta: dict of quiz-level settings from YAML frontmatter
        - questions_data: list of dicts, each representing a question with
          keys: question_name, question_type, points_possible, question_text,
          answers, correct_comments, incorrect_comments
    """
    # 1. Extract YAML frontmatter
    canvas_meta, body = _extract_frontmatter(content)
    
    # 2. Extract question blocks
    raw_blocks = _extract_question_blocks(body)
    
    # 3. Parse each question block
    questions = []
    for i, (attrs_str, block_content) in enumerate(raw_blocks):
        q = _parse_question_block(attrs_str, block_content, index=i)
        if q:
            questions.append(q)
    
    return canvas_meta, questions


def _extract_frontmatter(content):
    """
    Extract YAML frontmatter from the beginning of the file.
    Returns (canvas_meta dict, remaining body text).
    """
    match = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if match:
        raw_yaml = match.group(1)
        try:
            fm = yaml.safe_load(raw_yaml) or {}
        except yaml.YAMLError:
            fm = {}
        body = content[match.end():]
        return fm.get('canvas', {}), body
    return {}, content


def _extract_question_blocks(body):
    """
    Find all :::: {.question ...} ... :::: blocks in the body.
    
    Returns a list of (attributes_string, block_content) tuples.
    Supports optional indentation inside blocks.
    """
    blocks = []
    
    # Pattern: :::: {.question ...} at line start (with optional leading whitespace)
    # We need to handle nested ::: divs inside, so we track colon depth
    lines = body.split('\n')
    i = 0
    
    while i < len(lines):
        line = lines[i].strip()
        
        # Match opening: :::: {.question ...} or ::::{.question ...}
        open_match = re.match(r'^::::+\s*\{\.question(.*?)\}\s*$', line)
        if open_match:
            attrs_str = open_match.group(1).strip()
            block_lines = []
            depth = 1  # Track nesting of :::: blocks
            i += 1
            
            while i < len(lines) and depth > 0:
                stripped = lines[i].strip()
                
                # Count colons at start of stripped line
                colon_match = re.match(r'^(::::+)', stripped)
                if colon_match:
                    colons = len(colon_match.group(1))
                    rest = stripped[colons:].strip()
                    
                    if colons >= 4:
                        if rest == '' or rest.startswith('{') or rest.startswith('#'):
                            # Could be opening or closing
                            if rest == '':
                                # Closing ::::
                                depth -= 1
                                if depth == 0:
                                    break
                            else:
                                # Opening ::::
                                depth += 1
                                block_lines.append(lines[i])
                        else:
                            block_lines.append(lines[i])
                    else:
                        # ::: (3 colons) — inner divs, just include them
                        block_lines.append(lines[i])
                else:
                    block_lines.append(lines[i])
                
                i += 1
            
            block_content = '\n'.join(block_lines)
            # Strip common leading whitespace (optional indentation)
            block_content = _strip_indent(block_content)
            blocks.append((attrs_str, block_content))
        
        i += 1
    
    return blocks


def _strip_indent(text):
    """
    Remove common leading whitespace from all non-empty lines.
    This enables the optional indentation feature.
    """
    lines = text.split('\n')
    # Find minimum indent of non-empty lines
    min_indent = float('inf')
    for line in lines:
        if line.strip():  # Skip empty lines
            stripped = len(line) - len(line.lstrip())
            min_indent = min(min_indent, stripped)
    
    if min_indent == float('inf') or min_indent == 0:
        return text
    
    dedented = []
    for line in lines:
        if line.strip():
            dedented.append(line[min_indent:])
        else:
            dedented.append('')
    
    return '\n'.join(dedented)


def _parse_attributes(attrs_str):
    """
    Parse div attributes like: name="Spänning" points=2 type=essay_question
    Returns a dict.
    """
    attrs = {}
    # Match key="value" or key=value patterns
    for match in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*?)"|(\S+))', attrs_str):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        # Try to convert numeric values
        try:
            value = int(value)
        except (ValueError, TypeError):
            try:
                value = float(value)
            except (ValueError, TypeError):
                pass
        attrs[key] = value
    return attrs


def _parse_question_block(attrs_str, content, index=0):
    """
    Parse a single question block into a question dict.
    
    Handles both:
    - Checklist answers: - [x] / - [ ] with optional sub-item comments
    - Rich div answers: ::: {.answer ...} blocks
    """
    attrs = _parse_attributes(attrs_str)
    
    question = {
        'question_name': attrs.get('name', f'Fråga {index + 1}'),
        'question_type': attrs.get('type', 'multiple_choice_question'),
        'points_possible': attrs.get('points', 1),
    }
    
    # Detect answer format: div-based or checklist-based
    has_div_answers = re.search(r'^:::+\s*\{\.answer', content, re.MULTILINE)
    
    if has_div_answers:
        _parse_div_answers(content, question)
    else:
        _parse_checklist_answers(content, question)
    
    # Extract comment divs (correct-comment, incorrect-comment)
    _parse_comment_divs(content, question)
    
    return question


def _parse_checklist_answers(content, question):
    """
    Parse checklist-style answers:
      - [x] correct answer
        - Per-answer comment
      - [ ] wrong answer
    
    Sets question['question_text'] and question['answers'].
    """
    # Find the first checklist item to split question text from answers
    first_check = re.search(r'^(\s*)-\s*\[([ xX])\]\s*', content, re.MULTILINE)
    
    if first_check:
        question_text = content[:first_check.start()].strip()
        answers_section = content[first_check.start():]
    else:
        question_text = content.strip()
        question['answers'] = []
        question['question_text'] = _clean_question_text(question_text)
        return
    
    # Remove comment divs from question text
    question_text = _remove_comment_divs(question_text)
    question['question_text'] = _clean_question_text(question_text)
    
    # Parse individual checklist items
    # Pattern: - [x] or - [ ] at consistent indent, possibly followed by sub-items
    answers = []
    
    # Split into answer blocks: each starts with - [x] or - [ ]
    answer_pattern = re.compile(r'^(\s*)-\s*\[([ xX])\]\s*(.*?)(?=\n\s*-\s*\[[ xX]\]|\n\s*:::|\Z)', re.MULTILINE | re.DOTALL)
    
    # Remove comment divs from answers section before parsing
    answers_clean = _remove_comment_divs(answers_section)
    
    for match in answer_pattern.finditer(answers_clean):
        checked = match.group(2).lower() == 'x'
        answer_content = match.group(3).strip()
        
        # Check for sub-item comment (indented - below the answer)
        lines = answer_content.split('\n')
        answer_text = lines[0].strip()
        answer_comment = ''
        
        for line in lines[1:]:
            stripped = line.strip()
            # Sub-item: starts with - (after stripping indent)
            sub_match = re.match(r'^-\s+(.*)', stripped)
            if sub_match:
                answer_comment = sub_match.group(1).strip()
        
        answer_dict = {
            'answer_text': answer_text,
            'answer_weight': 100 if checked else 0,
        }
        if answer_comment:
            answer_dict['answer_comments'] = answer_comment
        
        answers.append(answer_dict)
    
    question['answers'] = answers


def _parse_div_answers(content, question):
    """
    Parse div-style answers:
      ::: {.answer correct=true comment="..."}
      Rich answer content
      :::
    
    Sets question['question_text'] and question['answers'].
    """
    # Find all ::: {.answer ...} blocks
    answers = []
    
    # First, find where the first answer div starts to split question text
    first_answer = re.search(r'^:::+\s*\{\.answer', content, re.MULTILINE)
    if first_answer:
        question_text = content[:first_answer.start()].strip()
    else:
        question_text = content.strip()
    
    # Remove comment divs from question text
    question_text = _remove_comment_divs(question_text)
    question['question_text'] = _clean_question_text(question_text)
    
    # Extract answer div blocks
    # Pattern: ::: {.answer ...} ... :::
    answer_blocks = _extract_inner_divs(content, 'answer')
    
    for attrs_str, answer_content in answer_blocks:
        attrs = _parse_attributes(attrs_str)
        
        # Check for 'correct' attribute (correct=true or .correct class)
        is_correct = False
        if attrs.get('correct') in (True, 'true', 'True', 1):
            is_correct = True
        # Also check for .correct class in the attrs string
        if '.correct' in attrs_str:
            is_correct = True
        
        answer_content = _strip_indent(answer_content).strip()
        
        answer_dict = {
            'answer_html': answer_content,  # Rich content, will be rendered to HTML  
            'answer_weight': 100 if is_correct else 0,
        }
        
        comment = attrs.get('comment', '')
        if comment:
            answer_dict['answer_comments'] = comment
        
        answers.append(answer_dict)
    
    question['answers'] = answers


def _extract_inner_divs(content, div_class):
    """
    Extract all ::: {.div_class ...} ... ::: blocks from content.
    Returns list of (attrs_str, inner_content) tuples.
    """
    results = []
    lines = content.split('\n')
    i = 0
    
    while i < len(lines):
        stripped = lines[i].strip()
        
        # Match opening ::: {.div_class ...}
        pattern = rf'^:::+\s*\{{\.{re.escape(div_class)}(.*?)\}}\s*$'
        open_match = re.match(pattern, stripped)
        
        if open_match:
            attrs_str = open_match.group(1).strip()
            inner_lines = []
            depth = 1
            i += 1
            
            while i < len(lines) and depth > 0:
                stripped_inner = lines[i].strip()
                colon_match = re.match(r'^(:::+)', stripped_inner)
                
                if colon_match:
                    colons = len(colon_match.group(1))
                    rest = stripped_inner[colons:].strip()
                    
                    if colons == 3 or colons == len(colon_match.group(1)):
                        if rest == '':
                            depth -= 1
                            if depth == 0:
                                break
                        elif rest.startswith('{'):
                            depth += 1
                            inner_lines.append(lines[i])
                        else:
                            inner_lines.append(lines[i])
                    else:
                        inner_lines.append(lines[i])
                else:
                    inner_lines.append(lines[i])
                
                i += 1
            
            inner_content = '\n'.join(inner_lines)
            results.append((attrs_str, _strip_indent(inner_content)))
        
        i += 1
    
    return results


def _parse_comment_divs(content, question):
    """
    Extract ::: correct-comment and ::: incorrect-comment divs.
    Sets question['correct_comments'] and question['incorrect_comments'].
    """
    for comment_type in ['correct-comment', 'incorrect-comment']:
        divs = _extract_named_divs(content, comment_type)
        if divs:
            # Use the first match
            key = comment_type.replace('-', '_').replace('comment', 'comments')
            question[key] = _strip_indent(divs[0]).strip()


def _extract_named_divs(content, div_name):
    """
    Extract all ::: div_name ... ::: blocks (without class syntax).
    Returns list of inner content strings.
    """
    results = []
    lines = content.split('\n')
    i = 0
    
    while i < len(lines):
        stripped = lines[i].strip()
        
        # Match opening ::: div_name or :::div_name
        pattern = rf'^:::+\s+{re.escape(div_name)}\s*$'
        if re.match(pattern, stripped):
            inner_lines = []
            depth = 1
            i += 1
            
            while i < len(lines) and depth > 0:
                stripped_inner = lines[i].strip()
                colon_match = re.match(r'^(:::+)\s*$', stripped_inner)
                
                if colon_match:
                    depth -= 1
                    if depth == 0:
                        break
                elif re.match(r'^:::+\s+\S', stripped_inner) or re.match(r'^:::+\s*\{', stripped_inner):
                    depth += 1
                    inner_lines.append(lines[i])
                else:
                    inner_lines.append(lines[i])
                
                i += 1
            
            results.append('\n'.join(inner_lines))
        
        i += 1
    
    return results


def _remove_comment_divs(text):
    """
    Remove ::: correct-comment and ::: incorrect-comment blocks from text.
    """
    for div_name in ['correct-comment', 'incorrect-comment']:
        # Remove the entire div block
        pattern = rf'^\s*:::+\s+{re.escape(div_name)}\s*\n.*?\n\s*:::+\s*$'
        text = re.sub(pattern, '', text, flags=re.MULTILINE | re.DOTALL)
    return text


def _clean_question_text(text):
    """
    Clean up question text: remove leading/trailing whitespace and blank lines.
    """
    # Remove leading/trailing blank lines
    lines = text.split('\n')
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return '\n'.join(lines)
