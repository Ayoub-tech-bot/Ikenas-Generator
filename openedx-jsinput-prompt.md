# Role

You are an Expert Open edX Developer and Educational Content Creator specializing in JSInput, `<customresponse>`, and interactive web-based exercises.

# Task

I will provide you with a **Course Topic**. Your job is to generate a complete, single-file HTML interactive exercise about this topic. The HTML must be visually engaging (using a fun, colorful CSS design similar to primary school educational apps) and MUST be perfectly compatible with my standard Open edX XML wrapper.

# Strict Technical Requirements

You must include the following Open edX integration logic exactly as specified.

### 1. Question Naming Convention (CRITICAL)

For all multiple-choice or fill-in-the-blank questions, the `data-q` attribute on the question container AND the `name` attribute on the radio inputs **must be the exact text of the question itself**, not a generic ID like "q1".

*Example HTML:*
```html
<div class="question" data-q="Le chien _____ dans le jardin.">
<input type="radio" name="Le chien _____ dans le jardin." value="aboie">
```

### 2. The JSChannel Import

You must import the JSChannel library at the end of the `<body>` before your custom script:
```html
<script src="https://plateforme.ikenas.com/static/js/capa/src/jschannel.js"></script>
```

### 3. State, Constants & The Answer Map

Define global variables for the correct answers. Because of the naming convention above, the keys in your `ANSWERS` object MUST be the exact question strings.
```javascript
var ANSWERS = { 
  "Le chien _____ dans le jardin.": "aboie",
  // Add generated questions here...
};
var TOTAL_QUESTIONS = /* Total number of gradable items */;
var state = { answers: {}, score: 0, total: TOTAL_QUESTIONS };
```
Note: If you include a Drag and Drop section, include a `DRAG_ANSWERS` object and add those to the `TOTAL_QUESTIONS` count as well.

### 4. The getGrade Function

Calculate the correct answers based on the user's input. You MUST return the result as a stringified JSON object with the keys `grade`, `score`, and `total`.
```javascript
function getGrade() {
  var correct = 0;
  
  // Calculate Radio button scores
  Object.keys(ANSWERS).forEach(function(q) {
    var sel = document.querySelector("input[name='" + q + "']:checked");
    var userAnswer = sel ? sel.value : "";
    state.answers[q] = { user_answer: userAnswer, correct_answer: ANSWERS[q] };
    if (userAnswer === ANSWERS[q]) correct++;
  });
  
  // [Insert Drag and Drop calculation logic here if applicable]

  state.score = correct;
  
  var gradePayload = {
    grade: correct / TOTAL_QUESTIONS,
    score: correct,
    total: TOTAL_QUESTIONS
  };
  return JSON.stringify(gradePayload);
}
```

### 5. The getState and setState Functions

Implement robust state management.

- **getState()**: Must return `JSON.stringify(state)`. Ensure it captures the current DOM inputs.
- **setState()**: Must parse the stringified state and visually update the DOM (checking radio buttons, replacing text in blanks) based on the exact question string keys. If you use blanks, include a helper function `getBlankIdFromQuestion(q)` that maps the literal question string to its HTML ID (e.g., `blank-1`).

### 6. JSChannel Binding

Bind the functions to the parent window exactly like this:
```javascript
window.MonExercice = {
  getGrade: getGrade,
  getState: getState,
  setState: setState,
};

if (window.parent !== window) {
  var ch = Channel.build({window: window.parent, origin: "*", scope: "JSInput"});
  ch.bind("getGrade", getGrade);
  ch.bind("getState", getState);
  ch.bind("setState", setState);
}
```

### 7. Dynamic Iframe Resizing (CRITICAL)

You must include the following code to ensure the iframe resizes dynamically in Open edX:
```javascript
function sendHeight() {
  var h = document.documentElement.scrollHeight || document.body.scrollHeight;
  window.parent.postMessage({type: "iframeResize", height: h + "px"}, "*");
}

window.addEventListener("load", sendHeight);

if (window.ResizeObserver) new ResizeObserver(sendHeight).observe(document.body);
```

# Execution

Please generate a fully designed, self-contained HTML file (including CSS and all required JS) for the following topic:

**Topic:** ["Les synonymes et les antonymes pour CE2"]
