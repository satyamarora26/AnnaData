import React, { useContext, useState, useRef, useEffect } from "react";
import { assets } from "../../assets/assets";
import Card from "../Card/Card";
import icon4 from "../../assets/message_icon.png";
import { IoMdSend } from "react-icons/io";
import { FaImage, FaMicrophone } from "react-icons/fa";
import { Context } from "../../Context/ContextProvider";
import Markdown from "markdown-to-jsx";
import MediaPreview from "../MediaPreview/MediaPreview";
import { getApiUrl } from "../../Config/api";

function Main() {
  const { onSent, responses, loading, setInput, input, recentPrompt, setResponses, setLoading } = useContext(Context);
  const { dataSent, setDataSent } = useContext(Context);

  const [selectedImage, setSelectedImage] = useState(null);
  const [selectedAudio, setSelectedAudio] = useState(null);

  const [isRecording, setIsRecording] = useState(false);
  const mediaRecorderRef = useRef(null);
  const audioChunksRef = useRef([]);

  const shouldSendRef = useRef(false);

  useEffect(() => {
    if (shouldSendRef.current && input.trim()) {
      onSent();
      shouldSendRef.current = false;
    }
  }, [input, onSent]);
  
  const clearSelectedImage = () => {
    setSelectedImage(null);
  };

  const clearSelectedAudio = () => {
    setSelectedAudio(null);
  };

  const handleImageChange = (e) => {
    const file = e.target.files[0];
    if (file) {
      setSelectedImage(file);
    }
  };
   const handleAudioRecord = async () => {
    if (isRecording) {
      // Stop the recording
      if (mediaRecorderRef.current) {
        mediaRecorderRef.current.stop();
        setIsRecording(false);
      }
    } else {
      // Start the recording
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        const recorder = new MediaRecorder(stream);
        
        // Store the recorder instance in the ref
        mediaRecorderRef.current = recorder;
        
        // Clear the audio chunks array in the ref
        audioChunksRef.current = [];

        recorder.ondataavailable = (e) => {
          if (e.data.size > 0) {
            audioChunksRef.current.push(e.data);
          }
        };

        recorder.onstop = async () => {
          const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
          setSelectedAudio(audioBlob);
          console.log("Audio recorded:", audioBlob);

          stream.getTracks().forEach(track => track.stop());
        };

        recorder.start();
        setIsRecording(true);
      } catch (err) {
        console.error("Error accessing microphone:", err);
      }
    }
  };

  const callDescribeAPI = async () => {
    if (!selectedImage && !selectedAudio) {
      console.log("No image or audio file selected.");
      return;
    }
    const formData = new FormData();
    if (selectedImage) {
      formData.append("image", selectedImage);
    }
    if (selectedAudio) {
      formData.append("audio", selectedAudio);
    }
    try {
      const response = await fetch(`${getApiUrl()}/api/chat/describe`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }
      
      const data = await response.json();
      console.log("API Response:", data);
      setSelectedImage(null);
      setSelectedAudio(null);
      
      return data.Result;
    } catch (error) {
      console.error("Failed to call the API:", error);
    }
  };

  const handleSend = async (e) => {
    e.preventDefault();
    setDataSent(true);

    if (selectedImage || selectedAudio) {
        setResponses((prevResponses) => [...prevResponses, { prompt: input, response: "Understanding Image/Audio files....", mediaFile: selectedImage || selectedAudio }]);
        setLoading(true);
        const description = await callDescribeAPI();
        shouldSendRef.current = true;
        setInput(`${input} (${description})`);
    } else if (input.trim()) {
      onSent();
    }
  };

  return (
    <div className="h-screen w-full flex flex-col overflow-hidden">

      {/* Main content area */}
      <div className="flex-grow flex flex-col relative overflow-hidden">
        <div className="bg-white flex flex-col w-full flex-grow overflow-y-auto p-4 items-center">
          {!dataSent ? (
            <div className="w-full flex-grow">
              <div className="mx-auto max-w-[844px] flex flex-col justify-start">
                <span className="w-full flex justify-center">
                  <h1 className="text-4xl sm:text-5xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-green-500 via-lime-500 to-yellow-500">
                    Annadata
                  </h1>
                </span>
                <span className="flex justify-start">
                  <p className="text-2xl sm:text-[48px] text-black opacity-30 font-semibold">
                    
                  </p>
                </span>
              </div>
              <div className="flex flex-col items-center mt-6 sm:mt-10">
                <div className="w-full">
                  <div className="mx-auto max-w-[850px]">
                    <div className="flex gap-4 overflow-x-auto scrollbar-hide md:grid md:grid-cols-4 md:gap-4">
                      <Card text="When should I irrigate my rice crop in Haryana" icon={icon4} />
                      <Card text="What is the best fertilizer schedule for wheat crop in Punjab" icon={icon4} />
                      <Card text="Which crops should I plant in Haryana to increase my profits?" icon={icon4} />
                      <Card text="Which cold storage service can i use to store my yield in Chhattisgarh" icon={icon4} />
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : (
            <div className="result flex-grow w-full flex flex-col items-center">
              {responses.map((response, index) => (
                <div key={index} className="response-item mb-4 w-full max-w-[800px]">
                  <div className="result-title flex justify-center mb-2">
                    <img src={assets.user_icon} alt="User" className="w-10 h-10 rounded-full mr-2" />
                    {response.mediaFile ? (
                      response.mediaFile.type.startsWith('image/') ? (
                          <img 
                              src={URL.createObjectURL(response.mediaFile)} 
                              alt="Uploaded" 
                              className="max-h-40 rounded-lg"
                          />
                      ) : (
                          <audio controls src={URL.createObjectURL(response.mediaFile)}></audio>
                      )
                    ) : (
                      <p className="text-xl font-semibold">{response.prompt}</p>
                    )}
                  </div>
                  <div className="result-data flex items-start">
                    {/* <img src={assets.gemini_icon} alt="Gemini" className="w-10 h-10 rounded-full mr-2" /> */}
                    <div className="p-4 rounded-lg shadow-sm flex-grow text-[15px] leading-9">
                      <Markdown>{response.response}</Markdown>
                      <hr />
                    </div>
                  </div>
                </div>
              ))}
              {loading && (
                <div className="w-full flex flex-col items-center gap-[10px]">
                  <div className="result-title flex items-center mb-2">
                    <img src={assets.user_icon} alt="User" className="w-10 h-10 rounded-full mr-2" />
                    <p className="text-xl font-semibold">{recentPrompt}</p>
                  </div>
                  <hr className="border-none w-[800px] h-[20px] bg-gradient-to-r from-[#9ed7ff] via-[#ffffff] to-[#9ed7ff] hr-animated" />
                  <hr className="border-none w-[800px] h-[20px] bg-gradient-to-r from-[#9ed7ff] via-[#ffffff] to-[#9ed7ff] hr-animated" />
                  <hr className="border-none w-[800px] h-[20px] bg-gradient-to-r from-[#9ed7ff] via-[#ffffff] to-[#9ed7ff] hr-animated" />
                </div>
              )}
            </div>
          )}
        </div>
      </div>
      
      {/* Input area */}
      <div className="px-4 bg-white sticky bottom-0">
        {!loading && <MediaPreview
          selectedImage={selectedImage}
          selectedAudio={selectedAudio}
          clearSelectedImage={clearSelectedImage}
          clearSelectedAudio={clearSelectedAudio}
        />}
        <form onSubmit={handleSend} className="flex items-center border rounded-full shadow-sm p-3 mx-auto bg-gray-100 w-full max-w-[750px]">
          <input
            onChange={(e) => setInput(e.target.value)}
            value={!loading ? input : ""}
            type="text"
            placeholder="Enter a prompt here"
            className="flex-grow bg-gray-100 outline-none text-gray-700 placeholder-gray-500"
          />
          <div className="flex items-center space-x-3 opacity-65">
            <label htmlFor="image-upload" className="cursor-pointer">
              <FaImage />
              <input
                id="image-upload"
                type="file"
                accept="image/*"
                onChange={handleImageChange}
                style={{ display: 'none' }}
              />
            </label>
            <button
              type="button"
              onClick={handleAudioRecord}
              className={isRecording ? 'text-red-500' : ''}
            >
              <FaMicrophone />
            </button>
            <button type="submit" disabled={loading}>
              <IoMdSend />
            </button>
          </div>
        </form>
        
        <br></br>
      </div>
    </div>
  );
}

export default Main;