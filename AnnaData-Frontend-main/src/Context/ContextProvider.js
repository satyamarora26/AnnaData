import { createContext, useState } from "react";
import run from "../Config/Gemini";

export const Context = createContext();

const ContextProvider = (props) => {
  const [input, setInput] = useState('');
  const [recentPrompt, setRecentPrompt] = useState('');
  const [previousPrompt, setPreviousPrompt] = useState([]);
  const [showResult, setShowResult] = useState(false);
  const [loading, setLoading] = useState(false);
  const [resultData, setResultData] = useState('');
  const [responses, setResponses] = useState([]);
  const [dataSent, setDataSent] = useState(false);

  const resetChat = () => {
    setResponses([]);
    setDataSent(false);
  };

  const onSent = async () => {
    if (!input.trim()) return;
    
    const history = responses.flatMap(item => {
        const historyArray = [];
        historyArray.push({
             role: "user",
             content: item.prompt
        });
        if (item.response) {
              historyArray.push({
                role: "assistant",
                content: item.response
              });
        }
        return historyArray;
    });
    const updatedHistory = [...history, { role: "user", content: input }];
    setLoading(true);
    setShowResult(true);
    setRecentPrompt(input);
    setPreviousPrompt((prevPrompts) => [...prevPrompts, input]);

    try {
      // Raw markdown is passed straight through; <Markdown> renders it.
      const response = await run(input, updatedHistory);
      const answer = response || "No response received.";
      setResponses((prevResponses) => [...prevResponses, { prompt: input, response: answer }]);
      setResultData(answer);
    } catch (error) {
      const errorMessage = "Error: " + error.message;
      const newResponse = { prompt: input, response: errorMessage };
      setResponses((prevResponses) => [...prevResponses, newResponse]);
      setResultData(errorMessage);
    }

    setLoading(false);
    setInput("");
  };

  const contextValue = {
    previousPrompt,
    setPreviousPrompt,
    onSent,
    setRecentPrompt,
    recentPrompt,
    showResult,
    loading,
    resultData,
    responses,
    setResponses,
    setLoading,
    input,
    setInput,
    resetChat,
    dataSent,
    setDataSent,
  };

  return (
    <Context.Provider value={contextValue}>{props.children}</Context.Provider>
  );
};

export default ContextProvider;
