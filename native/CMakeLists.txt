cmake_minimum_required(VERSION 3.26)
project(stokes_native LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

find_package(Python 3.13 COMPONENTS Interpreter Development.Module REQUIRED)
find_package(nanobind CONFIG REQUIRED)

nanobind_add_module(stokes_native NB_STATIC src/kernels.cpp)
install(TARGETS stokes_native LIBRARY DESTINATION .)
