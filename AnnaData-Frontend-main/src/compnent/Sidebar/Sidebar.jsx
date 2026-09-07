import React, { useState, useContext, useEffect } from "react";
import { FaPlus } from "react-icons/fa";
import { IoIosMenu } from "react-icons/io";
import { Context } from "../../Context/ContextProvider";

function Sidebar() {
  const [extended, setExtended] = useState(false);
  const { resetChat } = useContext(Context);

  const [location, setLocation] = useState(null);
  const [weatherData, setWeatherData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [locationName, setLocationName] = useState("Finding location...");

  useEffect(() => {
    const fetchWeather = async (lat, lon) => {
      try {
        const url = `https://api.open-meteo.com/v1/forecast?latitude=${lat}&longitude=${lon}&current_weather=true&daily=temperature_2m_max,temperature_2m_min,rain_sum,showers_sum,precipitation_probability_mean&forecast_days=7&timezone=Asia%2FKolkata`;
        
        const response = await fetch(url);
        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }
        const data = await response.json();
        setWeatherData(data);
        setLoading(false);
      } catch (e) {
        setError("Could not fetch weather data.");
        setLoading(false);
        console.error("Failed to fetch weather data:", e);
      }
    };
    const getReverseGeocode = async (lat, lon) => {
      try {
        const url = `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`;

        const response = await fetch(url);

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const data = await response.json();

        if (data.address) {
          const { city, village, town, state, country } = data.address;

          const locationName =
            city || village || town || data.display_name.split(",")[0];

          setLocationName(
            `${locationName}, ${state ? state + ", " : ""}${country || ""}`
          );
        } else {
          setLocationName("Location not found.");
        }
      } catch (e) {
        console.error("Failed to get location name:", e);
        setLocationName("Failed to get location name.");
      }
    };
    
    if (navigator.geolocation) {
      navigator.geolocation.getCurrentPosition(
        (position) => {
          const { latitude, longitude } = position.coords;
          setLocation({ latitude, longitude });
          fetchWeather(latitude, longitude);
          getReverseGeocode(latitude, longitude);
        },
        (err) => {
          setError("Location permission denied or unavailable.");
          setLoading(false);
          console.error("Geolocation error:", err);
        }
      );
    } else {
      setError("Geolocation is not supported by your browser.");
      setLoading(false);
    }
  }, []);

  const handleNewChat = () => {
    // Call the resetChat function from the context
    resetChat();
  };
  return (
    <div className="flex">
      {/* Menu icon for small screens */}
      <div className="lg:hidden fixed top-0 left-0 p-4 z-50">
        <IoIosMenu
          onClick={() => setExtended((prev) => !prev)}
          className="cursor-pointer w-8 h-8"
        />
      </div>

      {/* Sidebar */}
      <div
        className={`fixed top-0 left-0 h-screen ${
          extended ? "translate-x-0" : "-translate-x-full"
        } lg:translate-x-0 lg:static lg:flex lg:flex-col transition-all duration-300 ease-in-out ${
          extended ? "lg:w-60  sm:w-80" : "lg:w-16"
        } bg-gray-200 z-40`}
      >
        {/* Menu icon for large screens */}
        <div className="hidden lg:flex items-center mb-2 p-4">
          <IoIosMenu
            onClick={() => setExtended((prev) => !prev)}
            className={`cursor-pointer w-8 h-8  ${extended ? "ml-0" : "ml-1.5"}`}
          />
        </div>

        {/* Sidebar content */}
        <div className="flex flex-col px-3 pt-16 lg:pt-0 h-screen">
          <div className="flex-grow mb-4">
            <div
              className="flex items-center cursor-pointer mb-2 rounded-full gap-2 bg-gray-100 p-2"
              onClick={handleNewChat}
            >
              <FaPlus className={`w-4 h-4 ${extended ? "ml-0" : "ml-1.5"}`} />
              {extended && <p className="text-sm">New chat</p>}
            </div>
            <div className="mt-4 p-2 rounded-lg bg-gray-100">
            {extended && <h4 className="text-sm mb-2 font-semibold">Location & Weather</h4>}
            {extended && loading && <p className="text-xs text-gray-600">Finding your location...</p>}
            {extended && error && <p className="text-xs text-red-500">{error}</p>}
            {extended && location && weatherData && (
              <div className="text-xs">
              {location && (<p className="text-sm font-semibold mb-1">{locationName}</p>)}
                <p >Latitude: {location.latitude.toFixed(5)}</p>
                <p>Longitude: {location.longitude.toFixed(5)}</p>
                
                <p className="mt-2"><strong>Current Temp:</strong> {weatherData.current_weather.temperature}°C</p>
                
                <div className="mt-2">
                  <p className="font-semibold">Predicted Rainfall (Next 7 days):</p>
                  {weatherData.daily.rain_sum.map((rain, index) => (
                    <p key={index} className="text-xs ml-2">
                      Day {index + 1}: {rain.toFixed(1)} mm
                    </p>
                  ))}
                </div>
              </div>
            )}
          </div>
          </div>
          <div className={`flex flex-col space-y-3 ${extended ? "pl-0" : "pl-3"}`}>
          </div>
        </div>
      </div>
    </div>
  );
}

export default Sidebar;
